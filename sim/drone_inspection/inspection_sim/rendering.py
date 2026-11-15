from __future__ import annotations
from dataclasses import dataclass
import time
import uuid
import numpy as np
from scipy.spatial.transform import Rotation
from .coordinates import HABITAT_TO_WORLD,intrinsics,look_at,project

@dataclass
class Observation:
    id: str
    timestamp: float
    rgb: np.ndarray
    depth: np.ndarray
    position: np.ndarray
    rotation: np.ndarray
    intrinsics: dict

class HabitatRenderer:
    name='habitat'
    def __init__(self,geometry,config):
        import habitat_sim
        import magnum as mn
        self.hs=habitat_sim;self.mn=mn;self.config=config
        sim=habitat_sim.SimulatorConfiguration();sim.scene_id=str(geometry.habitat_glb);sim.enable_physics=False
        # Habitat infers MP3D GLBs as Z-up. Our exported asset is already Y-up;
        # override that inference so rendering matches physics and camera poses.
        metadata=habitat_sim.metadata.MetadataMediator()
        stages=metadata.stage_template_manager
        stage=stages.create_new_template(sim.scene_id,False)
        stage.orient_up=mn.Vector3(0,1,0)
        stage.orient_front=mn.Vector3(0,0,-1)
        stages.register_template(stage,sim.scene_id)
        agent=habitat_sim.agent.AgentConfiguration();agent.sensor_specifications=[]
        w,h=config.resolution
        for name,kind in [('rgb',habitat_sim.SensorType.COLOR),('depth',habitat_sim.SensorType.DEPTH)]:
            sensor=habitat_sim.CameraSensorSpec();sensor.uuid=name;sensor.sensor_type=kind
            sensor.sensor_subtype=habitat_sim.SensorSubType.PINHOLE;sensor.resolution=[h,w]
            sensor.position=mn.Vector3(0);sensor.hfov=mn.Deg(config.horizontal_fov)
            sensor.near=0.02;sensor.far=50.;agent.sensor_specifications.append(sensor)
        self.sim=habitat_sim.Simulator(habitat_sim.Configuration(sim,[agent],metadata_mediator=metadata))
        self.sim.initialize_agent(0)
        self.k=intrinsics(w,h,config.horizontal_fov)
    def capture(self,position,rotation,timestamp):
        from habitat_sim.utils.common import quat_from_coeffs
        state=self.hs.AgentState();state.position=(HABITAT_TO_WORLD.T@position).astype(np.float32)
        state.rotation=quat_from_coeffs(Rotation.from_matrix(HABITAT_TO_WORLD.T@rotation).as_quat())
        self.sim.get_agent(0).set_state(state,reset_sensors=True)
        observations=self.sim.get_sensor_observations()
        depth=np.array(observations['depth'],copy=True)
        depth[(depth<=0)|(depth>=49.9)]=np.nan
        return Observation(uuid.uuid4().hex,timestamp,np.array(observations['rgb'][...,:3],copy=True),depth,np.array(position),np.array(rotation),dict(self.k))
    def close(self): self.sim.close()

class BulletRenderer:
    """Explicit software-rendering fallback, never silently substituted for Habitat."""
    name='bullet'
    def __init__(self,geometry,config,physics):
        self.config=config;self.physics=physics
        self.k=intrinsics(*config.resolution,config.horizontal_fov)
    def capture(self,position,rotation,timestamp):
        import pybullet as p
        w,h=self.config.resolution;n,f=.02,50.
        view=p.computeViewMatrix(position,position-rotation[:,2],rotation[:,1])
        projection=p.computeProjectionMatrixFOV(self.k['vfov'],w/h,n,f)
        _,_,rgba,buffer,_=p.getCameraImage(w,h,viewMatrix=view,projectionMatrix=projection,renderer=p.ER_TINY_RENDERER,physicsClientId=self.physics.c)
        # PyBullet builds without NumPy support return flat tuples rather than image arrays.
        rgba=np.asarray(rgba,dtype=np.uint8).reshape(h,w,4)
        buffer=np.asarray(buffer,dtype=float).reshape(h,w)
        depth=f*n/(f-(f-n)*buffer);depth[depth>=49.9]=np.nan
        return Observation(uuid.uuid4().hex,timestamp,rgba[...,:3].copy(),depth,np.array(position),np.array(rotation),dict(self.k))
    def close(self): pass

class ChaseRenderer:
    """Third-person view through the same scene renderer as the onboard camera."""
    def __init__(self,renderer,config,physics):
        self.renderer=renderer;self.config=config;self.physics=physics

    def capture_chase(self,position,yaw,azimuth,distance,elevation,timestamp,camera_position,camera_rotation):
        # Shorten the camera boom before it crosses a wall. Try side views only
        # when the requested behind-camera direction has no usable clearance.
        eye=None
        for offset in [0,np.pi/4,-np.pi/4,np.pi/2,-np.pi/2,np.pi]:
            angle=yaw+azimuth+offset
            direction=np.array([-np.cos(angle)*np.cos(elevation),-np.sin(angle)*np.cos(elevation),np.sin(elevation)])
            for length in np.linspace(distance,min(.35,distance),12):
                candidate=camera_position+direction*length
                if self.physics.segment_clear(camera_position,candidate):
                    eye=candidate;break
            if eye is not None:break
        if eye is None:
            # A tiny offset remains within the already validated drone clearance.
            eye=np.asarray(position)+np.array([0,0,.04])
        target=camera_position-camera_rotation[:,2]*.2
        observation=self.renderer.capture(eye,look_at(eye,target),timestamp)
        observation.rgb=draw_camera_pose(observation,camera_position,camera_rotation,self.config.horizontal_fov)
        return observation


def draw_camera_pose(view,position,rotation,hfov):
    """Project a camera frustum into the third-person image with depth occlusion."""
    from PIL import Image,ImageDraw
    image=Image.fromarray(np.asarray(view.rgb,dtype=np.uint8));draw=ImageDraw.Draw(image)
    width,height=image.size
    def line(start,end,color,width_px=2):
        uv,depth=project(np.array([start,end]),view.position,view.rotation,view.intrinsics)
        if np.all(depth<=.02):return
        steps=min(1000,max(8,int(np.linalg.norm(uv[1]-uv[0]))+1))
        samples=np.linspace(start,end,steps)
        pixels,depths=project(samples,view.position,view.rotation,view.intrinsics)
        previous=None
        for pixel,z in zip(pixels,depths):
            x,y=pixel
            if z<=.02 or not (0<=x<width and 0<=y<height):previous=None;continue
            surface=view.depth[int(y),int(x)]
            if np.isfinite(surface) and z>surface+.035:previous=None;continue
            point=(float(x),float(y))
            if previous is not None:draw.line([previous,point],fill=color,width=width_px)
            previous=point
    blue=(126,162,255)
    depth=.55;half_width=depth*np.tan(np.deg2rad(hfov)/2)
    half_height=half_width*height/width
    corners=np.array([[-half_width,-half_height,-depth],[half_width,-half_height,-depth],
                      [half_width,half_height,-depth],[-half_width,half_height,-depth]])@rotation.T+position
    for index in range(4):
        line(position,corners[index],blue)
        line(corners[index],corners[(index+1)%4],blue)
    # Camera-local right, up and forward axes follow the full pose, including tilt.
    for direction,color in [(rotation[:,0],(232,120,74)),(rotation[:,1],(47,125,75)),(-rotation[:,2],blue)]:
        line(position,position+direction*.3,color,3)
    draw.rectangle([8,height-30,244,height-8],fill=(35,44,62))
    draw.text((16,height-25),'Camera pose | blue: field of view',fill=(233,236,243))
    return np.asarray(image).copy()
