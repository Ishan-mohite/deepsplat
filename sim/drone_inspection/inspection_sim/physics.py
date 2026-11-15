from __future__ import annotations
import sys
import math
import xml.etree.ElementTree as ET
import numpy as np
from scipy.spatial.transform import Rotation
from .config import ROOT
from .coordinates import body_command_to_world

sys.path.insert(0,str(ROOT/'vendor'))
import pybullet as p
from gym_pybullet_drones.control.DSLPIDControl import DSLPIDControl
from gym_pybullet_drones.utils.enums import DroneModel

class FlightPhysics:
    """Owns state. All calls occur on the simulation thread, never in the UI or agent."""
    radius=0.065
    def __init__(self,config,geometry,progress=None):
        self.config=config
        self.client=p.connect(p.DIRECT)
        self.c=self.client
        p.setGravity(0,0,-9.81,physicsClientId=self.c)
        p.setTimeStep(1/config.physics_hz,physicsClientId=self.c)
        p.setPhysicsEngineParameter(deterministicOverlappingPairs=1,numSolverIterations=80,physicsClientId=self.c)
        if progress:progress('Loading collision mesh: %s triangles'%len(geometry.mesh.faces))
        collision=p.createCollisionShape(p.GEOM_MESH,fileName=str(geometry.collision_obj),flags=p.GEOM_FORCE_CONCAVE_TRIMESH,physicsClientId=self.c)
        self.scene_body=p.createMultiBody(baseMass=0,baseCollisionShapeIndex=collision,physicsClientId=self.c)
        # Collision-only bodies otherwise get an automatic grey visual over the scene.
        p.changeVisualShape(self.scene_body,-1,rgbaColor=[0,0,0,0],physicsClientId=self.c)
        for index,mesh in enumerate(geometry.meshes):
            if progress:progress('Loading scene visuals: %d / %d'%(index+1,len(geometry.meshes)))
            color=np.array([155,165,184,255])
            if hasattr(mesh.visual,'face_colors') and len(mesh.visual.face_colors): color=mesh.visual.face_colors.mean(axis=0)
            folder=geometry.cache/('visual_%d'%index);folder.mkdir(exist_ok=True)
            # Keep the software fallback lightweight; Habitat renders scan textures.
            path=folder/'mesh_plain.obj'
            if not path.exists():mesh.export(path,include_texture=False)
            visual=p.createVisualShape(p.GEOM_MESH,fileName=str(path),rgbaColor=(color/255).tolist(),physicsClientId=self.c)
            p.createMultiBody(baseMass=0,baseVisualShapeIndex=visual,physicsClientId=self.c)
        self.urdf=ROOT/'vendor/gym_pybullet_drones/assets/cf2x.urdf'
        self.drone=p.loadURDF(str(self.urdf),config.initial_position,config.initial_quaternion_xyzw,flags=p.URDF_USE_INERTIA_FROM_FILE,physicsClientId=self.c)
        p.changeDynamics(self.drone,-1,linearDamping=0,angularDamping=0,physicsClientId=self.c)
        p.setCollisionFilterGroupMask(self.scene_body,-1,1,3,physicsClientId=self.c)
        for link in range(-1,p.getNumJoints(self.drone,physicsClientId=self.c)):
            p.setCollisionFilterGroupMask(self.drone,link,2,1,physicsClientId=self.c)
        tree=ET.parse(self.urdf).getroot(); prop=tree.find('properties'); inertial=tree.find('link/inertial')
        self.mass=float(inertial.find('mass').get('value'))
        self.kf=float(prop.get('kf')); self.km=float(prop.get('km')); arm=float(prop.get('arm'))
        self.motors=np.array([[float(x) for x in tree.find("link[@name='prop%d_link']/inertial/origin"%i).get('xyz').split()] for i in range(4)])
        # The upstream PID mixer uses this X layout; reaction torques alternate -,+,-,+.
        self.max_rpm=math.sqrt(float(prop.get('thrust2weight'))*self.mass*9.81/(4*self.kf))
        self.controller=DSLPIDControl(DroneModel.CF2X,g=9.81)
        self.rpm=np.zeros(4);self.velocity_command=np.zeros(3);self.target=np.array(config.initial_position,dtype=float)
        self.target_yaw=Rotation.from_quat(config.initial_quaternion_xyzw).as_euler('xyz')[2]
        self.sim_time=0.;self.collided=False;self.last_blocked=False
        query=p.createCollisionShape(p.GEOM_SPHERE,radius=self.radius+config.clearance,physicsClientId=self.c)
        self.probe=p.createMultiBody(baseMass=0,baseCollisionShapeIndex=query,basePosition=[0,0,-10000],physicsClientId=self.c)
        p.changeVisualShape(self.probe,-1,rgbaColor=[0,0,0,0],physicsClientId=self.c)
        p.setCollisionFilterGroupMask(self.probe,-1,0,0,physicsClientId=self.c)
        self.asset_report=dict(model='CF2X',source_revision='410c2395947fb850cfcdf7a2b7f17be0eac44a7e',license='MIT',
            urdf=str(self.urdf.relative_to(ROOT)),geometry_scale=1.0,mass_kg=self.mass,
            inertia_kg_m2={k:float(v) for k,v in inertial.find('inertia').attrib.items()},motor_locations_m=self.motors.tolist(),
            kf_N_per_RPM2=self.kf,km_Nm_per_RPM2=self.km,max_rpm=self.max_rpm,controller='upstream DSLPIDControl',
            physics='PyBullet 3.2.7, motor thrust and reaction torques; no downwash, aerodynamic drag or ground effect')
        if not self.is_free(config.initial_position): raise ValueError('Supplied initial pose is not in free space with configured clearance')

    def state(self):
        position,quaternion=p.getBasePositionAndOrientation(self.drone,physicsClientId=self.c)
        velocity,omega=p.getBaseVelocity(self.drone,physicsClientId=self.c)
        return np.array(position),np.array(quaternion),np.array(velocity),np.array(omega)

    def is_free(self,position):
        p.resetBasePositionAndOrientation(self.probe,np.asarray(position).tolist(),[0,0,0,1],physicsClientId=self.c)
        return not p.getClosestPoints(self.probe,self.scene_body,distance=0,physicsClientId=self.c)

    def segment_clear(self,start,end):
        distance=np.linalg.norm(np.array(end)-start)
        n=max(2,int(math.ceil(distance/0.08))+1)
        return all(self.is_free(point) for point in np.linspace(start,end,n))

    def visible(self,eye,points):
        if not len(points): return np.zeros(0,dtype=bool)
        # Ignore the drone and query sphere: only the aligned static mesh is an occluder.
        results=p.rayTestBatch([np.asarray(eye).tolist()]*len(points),np.asarray(points).tolist(),collisionFilterMask=1,physicsClientId=self.c)
        distances=np.linalg.norm(np.asarray(points)-eye,axis=1)
        return np.array([hit[0] == -1 or (hit[0]==self.scene_body and (1-hit[2])*distance < 0.025) for hit,distance in zip(results,distances)])

    def control(self,command,frame='world'):
        command=np.asarray(command,dtype=float)
        if command.shape!=(4,) or not np.isfinite(command).all(): raise ValueError('Velocity must contain four finite values')
        pos,quat,velocity,omega=self.state();yaw=Rotation.from_quat(quat).as_euler('xyz')[2]
        if frame=='body_horizontal_world_vertical': command=body_command_to_world(command,yaw)
        elif frame!='world': raise ValueError('Unknown command frame')
        speed=np.linalg.norm(command[:3])
        if speed>self.config.max_speed: command[:3]*=self.config.max_speed/speed
        command[3]=np.clip(command[3],-self.config.max_yaw_rate,self.config.max_yaw_rate)
        braking=np.linalg.norm(velocity)**2/(2*self.config.max_acceleration)+0.25
        lookahead=max(0.35,braking/max(np.linalg.norm(command[:3]),0.1))
        self.last_blocked=not self.segment_clear(pos,pos+command[:3]*lookahead)
        if self.last_blocked: command[:3]=0
        dt=1/self.config.control_hz
        difference=command[:3]-self.velocity_command
        norm=np.linalg.norm(difference)
        if norm>self.config.max_acceleration*dt: difference*=self.config.max_acceleration*dt/norm
        self.velocity_command+=difference
        if np.linalg.norm(self.velocity_command)>1e-4:
            self.target=pos+self.velocity_command*0.25
        # When stopped, keep the last hold point; do not teleport or directly set base velocity.
        self.target_yaw+=command[3]*dt
        rpm,_,_=self.controller.computeControl(dt,pos,quat,velocity,omega,self.target,np.array([0,0,self.target_yaw]),self.velocity_command,np.array([0,0,command[3]]))
        self.rpm=np.clip(rpm,0,self.max_rpm)

    def step(self):
        thrust=self.kf*self.rpm**2
        for force,location in zip(thrust,self.motors):
            p.applyExternalForce(self.drone,-1,[0,0,float(force)],location.tolist(),p.LINK_FRAME,physicsClientId=self.c)
        torque=self.km*float(np.dot(self.rpm**2,[-1,1,-1,1]))
        p.applyExternalTorque(self.drone,-1,[0,0,torque],p.LINK_FRAME,physicsClientId=self.c)
        p.stepSimulation(physicsClientId=self.c);self.sim_time+=1/self.config.physics_hz
        self.collided=bool(p.getContactPoints(self.drone,self.scene_body,physicsClientId=self.c))

    def hover(self):
        self.target=self.state()[0].copy();self.velocity_command[:]=0

    def reset(self,position=None,quaternion=None):
        pos=self.config.initial_position if position is None else position
        quat=self.config.initial_quaternion_xyzw if quaternion is None else quaternion
        if not self.is_free(pos): raise ValueError('Starting pose violates obstacle clearance')
        p.resetBasePositionAndOrientation(self.drone,pos,quat,physicsClientId=self.c)
        p.resetBaseVelocity(self.drone,[0,0,0],[0,0,0],physicsClientId=self.c)
        self.controller.reset();self.target=np.array(pos,dtype=float);self.velocity_command[:]=0
        self.target_yaw=Rotation.from_quat(quat).as_euler('xyz')[2]
        self.sim_time=0;self.collided=False

    def close(self): p.disconnect(self.c)
