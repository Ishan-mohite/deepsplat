from __future__ import annotations
from collections import OrderedDict
from datetime import datetime,timezone
from pathlib import Path
import hashlib
import io
import json
import math
import queue
import threading
import time
import traceback
import uuid
import numpy as np
from PIL import Image
from scipy.spatial.transform import Rotation
from .config import ROOT
from .coordinates import camera_pose,unproject,intrinsics
from .scene import SceneGeometry
from .physics import FlightPhysics
from .rendering import HabitatRenderer,BulletRenderer,ChaseRenderer
from .coverage import Coverage
from .planning import OccupancyGrid,InspectionPlanner

class Simulation:
    def __init__(self,config,ui_watchdog=True):
        self.config=config;self.ui_watchdog=ui_watchdog;self.commands=queue.Queue();self.lock=threading.RLock()
        self.snapshot={'ready':False,'state':'initializing','action':'Loading scene','error':None}
        self.images={};self.frames=OrderedDict();self.frame_lock=threading.RLock();self.stop_event=threading.Event()
        self.thread=None;self.geometry=None;self.physics=None;self.renderer=None
        self.mode='autonomous';self.state='idle';self.paused=False;self.emergency=False;self.manual_override=False
        self.manual=np.zeros(4);self.manual_frame='body_horizontal_world_vertical';self.manual_at=0;self.heartbeat_at=time.monotonic()
        self.selection=None;self.active_view=None;self.route=[];self.view_hold=0.;self.reason=None
        self.chat=[];self.command_history=[];self.trajectory=[];self.run_folder=None;self.run_start=0.;self.run_elapsed=0.;self.last_saved_image=-99
        self.chase_settings=[0.0,1.6,.45];self.error=None;self.action='Ready';self.collision_count=0
        self.view_settings={'show_trajectory':False,'show_urdf':True,'show_fov':False}

    def initialize(self):
        def loading(message):self.publish(ready=False,action=message)
        self.geometry=SceneGeometry(self.config,progress=loading)
        self.physics=FlightPhysics(self.config,self.geometry,progress=loading)
        loading('Starting camera renderer')
        self.renderer=self.make_renderer();self.chase=ChaseRenderer(self.renderer,self.config,self.physics)
        self.coverage=Coverage(self.geometry,self.config)
        self.render()
        def progress(done,total):
            self.publish(ready=False,action='Building navigation grid: %d / %d cells (%.0f%%)'%(done,total,100*done/total))
        self.publish(ready=False,action='Building navigation grid; camera preview is available')
        self.grid=OccupancyGrid(self.geometry,self.physics,self.config,progress=progress)
        self.geometry.report['spawn']=self.grid.validate_start(self.config.initial_position)
        self.planner=InspectionPlanner(self.coverage,self.grid,self.physics,self.config)
        self.render();self.publish()

    def make_renderer(self):
        if self.config.renderer=='habitat': return HabitatRenderer(self.geometry,self.config)
        return BulletRenderer(self.geometry,self.config,self.physics)

    def start(self):
        self.thread=threading.Thread(target=self.loop,name='inspection-simulation',daemon=True);self.thread.start()

    def submit(self,command):
        if self.commands.qsize()>200: raise ValueError('Command queue is full')
        self.commands.put(command)

    def loop(self):
        try:
            self.initialize();last_render=time.monotonic();last_publish=0;counter=0
            while not self.stop_event.is_set():
                begin=time.monotonic()
                for _ in range(200):
                    try: command=self.commands.get_nowait()
                    except queue.Empty:break
                    try:self.apply(command)
                    except Exception as error:self.error=str(error);self.publish()
                if not self.paused and not self.emergency:
                    if counter%(self.config.physics_hz//self.config.control_hz)==0:self.control()
                    if not self.paused and not self.emergency:
                        self.physics.step();counter+=1
                        if self.physics.collided:
                            self.collision_count+=1;self.emergency=True;self.finish('collision','partial')
                            self.action='Collision: physics paused. Reset to continue.'
                now=time.monotonic()
                if now-last_render>=1/self.config.render_hz:
                    self.render();last_render=time.monotonic()
                if now-last_publish>=.1:self.publish();last_publish=now
                self.stop_event.wait(max(0,1/self.config.physics_hz-(time.monotonic()-begin)))
        except Exception as error:
            self.error=str(error);self.publish(ready=False,state='error',action='Initialization failed');traceback.print_exc()
        finally:
            if self.run_folder:self.save_run()
            if self.renderer:self.renderer.close()
            if self.physics:self.physics.close()

    def apply(self,command):
        name=command.get('type')
        if name=='heartbeat':self.heartbeat_at=time.monotonic();return
        self.command_history.append({'sim_time':self.physics.sim_time,'wall_time':time.time(),'command':command})
        self.error=None
        if name=='velocity':
            vector=np.asarray(command.get('value'),dtype=float)
            if vector.shape!=(4,) or not np.isfinite(vector).all():raise ValueError('vel_cmd must have four finite numbers')
            frame=command.get('frame','body_horizontal_world_vertical')
            if frame not in ('body_horizontal_world_vertical','world'):raise ValueError('Invalid command frame')
            self.manual=vector;self.manual_frame=frame;self.manual_at=time.monotonic()
            self.manual_override=True;self.active_view=None;self.route=[];self.action='Manual override; stale commands fall back to hover'
        elif name=='hover':
            self.manual[:]=0;self.manual_at=time.monotonic();self.manual_override=True
            self.physics.hover();self.active_view=None;self.route=[];self.action='Controlled hover (manual override)'
        elif name=='pause':self.paused=True;self.action='Physics paused'
        elif name=='resume':
            if self.emergency:raise ValueError('Reset is required after emergency stop or collision')
            self.paused=False;self.manual_override=False;self.active_view=None;self.route=[];self.physics.hover()
            self.heartbeat_at=time.monotonic();self.action='Resumed; replanning from current pose'
        elif name in ('cancel','stop'):self.stop_inspection()
        elif name=='emergency_stop':self.emergency=True;self.paused=True;self.finish('emergency_stop','cancelled');self.action='Emergency stop: physics frozen'
        elif name=='reset':
            if self.run_folder:self.save_run()
            position=command.get('position',self.config.initial_position);quaternion=command.get('quaternion',self.config.initial_quaternion_xyzw)
            if len(position)!=3 or not np.isfinite(position).all() or len(quaternion)!=4 or not np.isfinite(quaternion).all() or abs(np.linalg.norm(quaternion)-1)>1e-3:raise ValueError('Reset requires a finite position and unit xyzw quaternion')
            self.physics.reset(position,quaternion);self.config.initial_position=list(position);self.config.initial_quaternion_xyzw=list(quaternion)
            self.coverage=Coverage(self.geometry,self.config);self.planner=InspectionPlanner(self.coverage,self.grid,self.physics,self.config)
            self.state='idle';self.paused=False;self.emergency=False;self.manual_override=False;self.manual[:]=0
            self.route=[];self.active_view=None;self.selection=None;self.reason=None;self.run_folder=None;self.run_elapsed=0.
            self.trajectory=[];self.command_history=[];self.collision_count=0;self.action='Reset to supplied starting pose';self.heartbeat_at=time.monotonic()
        elif name=='camera':
            if self.state=='running':raise ValueError('Pause or finish the task before changing camera settings')
            values=self.config.dictionary();values['horizontal_fov']=float(command['hfov']);values['resolution']=command['resolution']
            updated=type(self.config)(**values);updated.validate()
            self.config.horizontal_fov=updated.horizontal_fov;self.config.resolution=updated.resolution
            self.renderer.close();self.renderer=self.make_renderer();self.chase=ChaseRenderer(self.renderer,self.config,self.physics)
            self.render();self.action='Camera image and intrinsics updated together'
        elif name=='limits':
            if self.state=='running':raise ValueError('Finish or cancel the task before changing limits')
            speed=float(command['max_speed']);accel=float(command['max_acceleration']);yaw=float(command['max_yaw_rate'])
            if not np.isfinite([speed,accel,yaw]).all() or not 0<speed<=3 or not 0<accel<=3 or not 0<yaw<=3:raise ValueError('Limits must be finite and in (0,3]')
            self.config.max_speed=speed;self.config.max_acceleration=accel;self.config.max_yaw_rate=yaw
            if 'clearance' in command:
                clearance=float(command['clearance'])
                if not np.isfinite(clearance) or not .05<=clearance<=.8:raise ValueError('Clearance must be .05–.8 metres')
                if abs(clearance-self.config.clearance)>1e-6:raise ValueError('Clearance is configured at startup because changing it requires rebuilding the collision probe and occupancy grid')
        elif name=='select':self.select(command)
        elif name=='view':
            changes={key:value for key,value in command.items() if key!='type'}
            if not changes or set(changes)-set(self.view_settings) or any(type(value) is not bool for value in changes.values()):
                raise ValueError('View settings must be boolean trajectory, URDF or FOV toggles')
            updated={**self.view_settings,**changes}
            if not updated['show_urdf'] and not updated['show_fov']:
                raise ValueError('Keep at least one of Drone URDF or FOV visible')
            self.view_settings=updated
        elif name=='start':self.begin_task(command)
        elif name=='chase':
            values=np.asarray([command.get('azimuth',0),command.get('distance',1.6),command.get('elevation',.45)],dtype=float)
            if not np.isfinite(values).all():raise ValueError('Invalid chase camera settings')
            self.chase_settings=[float(values[0]),float(np.clip(values[1],.4,6)),float(np.clip(values[2],.05,1.3))]
        elif name=='save':self.save_run()
        else:raise ValueError('Unsupported command type: '+str(name))
        self.publish()

    def select(self,command):
        if self.state=='running':raise ValueError('Cancel/reset the task before changing the frozen target definition')
        with self.frame_lock:frame=self.frames.get(command.get('frame_id'))
        if frame is None:raise ValueError('Frozen frame expired. Freeze a fresh first-person image.')
        radius=float(command.get('radius',self.config.selection_radius))
        if not np.isfinite(radius) or not .1<=radius<=2:raise ValueError('Region radius must be .1–2 metres')
        if 'box' in command:
            x0,y0,x1,y1=command['box']
            pixels=[(x,y) for x in np.linspace(x0,x1,5) for y in np.linspace(y0,y1,5)]
        else:pixels=[(command['u'],command['v'])]
        points=[]
        for u,v in pixels:
            if not np.isfinite([u,v]).all() or not 0<=u<frame.intrinsics['width'] or not 0<=v<frame.intrinsics['height']:raise ValueError('Pixel is outside frozen frame')
            depth=float(frame.depth[min(int(v),frame.depth.shape[0]-1),min(int(u),frame.depth.shape[1]-1)])
            if np.isfinite(depth) and .02<depth<49.9:points.append(unproject([u,v],depth,frame.position,frame.rotation,frame.intrinsics))
        if not points:raise ValueError('Selection has no valid surface hit')
        center=np.mean(points,axis=0)
        if len(points)>1:radius=max(radius,float(np.linalg.norm(points-center,axis=1).max()))
        radius=min(radius,3.)
        self.coverage.started=False;self.coverage.define(center,radius)
        self.selection={'center':center.tolist(),'radius':radius,'source_frame':frame.id,'capture_time':frame.timestamp,'bounds':'3D sphere shown on map; confirm with Start inspection'}
        self.mode='user-directed';self.action='Region preview ready; Start inspection to confirm its extent'

    def begin_task(self,command):
        if self.emergency:raise ValueError('Reset after emergency stop before starting a task')
        if self.state=='running':raise ValueError('Task already running; target definition is frozen')
        mode=command.get('mode','autonomous')
        if mode not in ('autonomous','user-directed'):raise ValueError('Unknown inspection mode')
        self.coverage.started=False
        if mode=='user-directed':
            if self.selection is None:raise ValueError('Freeze a first-person frame and preview a target region first')
            self.coverage.define(np.array(self.selection['center']),self.selection['radius'])
        else:self.coverage.define();self.selection=None
        self.coverage.started=True;self.planner=InspectionPlanner(self.coverage,self.grid,self.physics,self.config)
        self.mode=mode;self.state='running';self.reason=None;self.paused=False;self.manual_override=False
        self.active_view=None;self.route=[];self.run_start=self.physics.sim_time;self.run_elapsed=0.;self.heartbeat_at=time.monotonic()
        self.trajectory=[];self.command_history=[]
        tag=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8]
        self.run_folder=ROOT/'runs'/tag;self.run_folder.mkdir(parents=True)
        self.action='Planning collision-free inspection viewpoints'

    def control(self):
        now=time.monotonic();pos,quat,velocity,_=self.physics.state()
        yaw=Rotation.from_quat(quat).as_euler('xyz')[2]
        if self.ui_watchdog and self.state=='running' and now-self.heartbeat_at>1.5:
            self.manual_override=True;self.manual[:]=0;self.action='UI connection lost: controlled hover; resume to replan'
        command=np.zeros(4);frame='world'
        if self.manual_override:
            if now-self.manual_at<self.config.command_timeout:
                command=self.manual.copy();frame=self.manual_frame
        elif self.state=='running':
            if self.physics.sim_time-self.run_start>=self.config.max_sim_seconds:self.finish('simulation_time_budget','partial')
            elif self.planner.decisions>=self.config.max_planner_steps:self.finish('viewpoint_budget','partial')
            else:
                if self.active_view is None:
                    self.active_view=self.planner.next_view(pos,self.renderer.k)
                    if self.active_view is None:
                        self.coverage.unreachable[self.coverage.target&~self.coverage.covered]=True
                        self.finish('no_feasible_viewpoint_in_bounded_candidate_search','partial')
                    else:
                        self.route=list(self.active_view['route']);self.view_hold=0
                        self.action='Inspecting a viewpoint with %.2f m² predicted new visible area'%self.active_view['expected_area']
                if self.active_view is not None:
                    while self.route and np.linalg.norm(self.route[0]-pos)<.14:self.route.pop(0)
                    if self.route:
                        delta=self.route[0]-pos;distance=np.linalg.norm(delta)
                        command[:3]=delta/max(distance,1e-9)*min(self.config.max_speed,distance*1.2)
                    command[3]=float(np.clip(2*angle_difference(self.active_view['yaw'],yaw),-self.config.max_yaw_rate,self.config.max_yaw_rate))
                    if not self.route and abs(command[3])<.12 and np.linalg.norm(velocity)<.12:
                        self.view_hold+=1/self.config.control_hz
                        if self.view_hold>=1.0:self.active_view=None
        self.physics.control(command,frame)
        self.trajectory.append({'t':self.physics.sim_time,'position':pos.tolist(),'quaternion_xyzw':quat.tolist(),'velocity':velocity.tolist(),'rpm':self.physics.rpm.tolist()})
        if len(self.trajectory)>120000:self.trajectory=self.trajectory[-120000:]

    def render(self):
        started=time.perf_counter();pos,quat,_,_=self.physics.state();eye,rotation=camera_pose(pos,quat)
        observation=self.renderer.capture(eye,rotation,self.physics.sim_time)
        self.coverage.observe(observation,self.physics.visible)
        if self.state=='running' and self.coverage.summary()['fraction']>=self.config.coverage_threshold:self.finish('coverage_threshold','completed')
        yaw=Rotation.from_quat(quat).as_euler('xyz')[2]
        chase=self.chase.capture_chase(pos,yaw,*self.chase_settings,self.physics.sim_time,eye,rotation,settings=self.view_settings,route=self.route)
        with self.frame_lock:
            self.frames[observation.id]=observation
            while len(self.frames)>40:self.frames.popitem(last=False)
            self.last_frame_id=observation.id
        with self.lock:
            self.images['first']=jpeg(observation.rgb);self.images['third']=jpeg(chase.rgb)
        self.render_ms=(time.perf_counter()-started)*1000
        if self.run_folder and self.physics.sim_time-self.last_saved_image>=2:
            (self.run_folder/('frame_%.3f.jpg'%observation.timestamp)).write_bytes(jpeg(observation.rgb));self.last_saved_image=self.physics.sim_time

    def finish(self,reason,state):
        if self.coverage.started:self.run_elapsed=max(0,self.physics.sim_time-self.run_start)
        self.coverage.started=False
        self.state=state;self.reason=reason;self.route=[];self.active_view=None;self.manual_override=False
        self.action='Task %s: %s'%(state,reason.replace('_',' '))
        if self.run_folder:self.save_run()

    def stop_inspection(self):
        # Save the stopped run before clearing the live inspection baseline.
        self.finish('cancelled','cancelled')
        self.run_folder=None;self.coverage.define();self.selection=None
        self.planner=InspectionPlanner(self.coverage,self.grid,self.physics,self.config)
        self.state='idle';self.mode='manual';self.reason=None;self.run_elapsed=0.;self.view_hold=0.
        self.trajectory=[];self.command_history=[];self.manual[:]=0
        self.manual_at=time.monotonic();self.manual_override=True;self.physics.hover()
        if not self.emergency:self.paused=False
        self.action='Inspection stopped; progress reset. '+('Reset is required after emergency stop or collision.' if self.emergency else 'Manual flight is available.')

    def save_run(self):
        if not self.geometry:return
        if self.run_folder is None:
            self.run_folder=ROOT/'runs'/('snapshot_'+uuid.uuid4().hex[:8]);self.run_folder.mkdir(parents=True)
        report={'schema_version':1,'config':self.config.dictionary(),'scene':self.geometry.report,'drone':self.physics.asset_report,
                'mode':self.mode,'state':self.state,'completion_reason':self.reason,'selection':self.selection,
                'coverage':self.coverage.summary(),'coverage_state':{'points':self.coverage.points.tolist(),'areas_m2':self.coverage.areas.tolist(),'colors':self.coverage.colors()},
                'trajectory':self.trajectory,'command_history':self.command_history,'chat':self.chat,
                'simulation_seconds':self.physics.sim_time,'render_ms':getattr(self,'render_ms',None)}
        temp=self.run_folder/'run.json.tmp';temp.write_text(json.dumps(report,indent=2,allow_nan=False));temp.replace(self.run_folder/'run.json')

    def publish(self,**overrides):
        if self.physics and hasattr(self,'coverage'):
            pos,quat,velocity,_=self.physics.state();eye,rotation=camera_pose(pos,quat)
            value={'ready':True,'state':self.state,'paused':self.paused,'emergency':self.emergency,'mode':self.mode,
                   'view_settings':dict(self.view_settings),
                   'manual_override':self.manual_override,'action':self.action,'error':self.error,'completion_reason':self.reason,
                   'position':pos.tolist(),'quaternion_xyzw':quat.tolist(),'speed':float(np.linalg.norm(velocity)),'altitude':float(pos[2]),
                   'collision':self.physics.collided,'collision_count':self.collision_count,'clearance_blocked':self.physics.last_blocked,
                   'sim_time':self.physics.sim_time,'elapsed':max(0,self.physics.sim_time-self.run_start) if self.state=='running' else self.run_elapsed,
                   'frame_id':getattr(self,'last_frame_id',None),'camera':{'position':eye.tolist(),'rotation':rotation.tolist(),'intrinsics':self.renderer.k,'renderer':self.renderer.name},
                   'coverage':self.coverage.summary(),'points':self.coverage.points.tolist(),'colors':self.coverage.colors(),
                   'route':[point.tolist() for point in self.route],'target':self.active_view['target'].tolist() if self.active_view else None,
                   'selection':self.selection,'scene':self.geometry.report,'config':self.config.dictionary(),
                   'render_ms':getattr(self,'render_ms',0),'planner_decisions':self.planner.decisions if hasattr(self,'planner') else 0,
                   'run_folder':str(self.run_folder.relative_to(ROOT)) if self.run_folder else None,'chat':list(self.chat[-40:])}
        else:value=dict(self.snapshot)
        value.update(overrides)
        with self.lock:self.snapshot=value

    def read(self):
        with self.lock:return dict(self.snapshot)
    def close(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=10)

def angle_difference(target,current):return (target-current+math.pi)%(2*math.pi)-math.pi

def jpeg(rgb):
    buffer=io.BytesIO();Image.fromarray(np.asarray(rgb,dtype=np.uint8)).save(buffer,format='JPEG',quality=85);return buffer.getvalue()
