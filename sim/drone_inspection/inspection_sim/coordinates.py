"""Canonical world: metres, right-handed, +Z up. Body +X forward, +Y left, +Z up.
Habitat/GLTF: +Y up, camera looks -Z; Three.js map uses the canonical Z-up frame.
All quaternions exposed by this project are [x,y,z,w].
"""
import numpy as np
from scipy.spatial.transform import Rotation

HABITAT_TO_WORLD = np.array([[1.,0,0],[0,0,-1.],[0,1.,0]])
CAMERA_TO_BODY = np.array([[0.,0,-1.],[-1.,0,0],[0,1.,0]])

def camera_pose(position, quaternion):
    r=Rotation.from_quat(quaternion).as_matrix()
    return np.asarray(position)+r@np.array([0.08,0,0]), r@CAMERA_TO_BODY

def body_command_to_world(command, yaw):
    x,y,z,rate=command
    c,s=np.cos(yaw),np.sin(yaw)
    return np.array([c*x-s*y,s*x+c*y,z,rate],dtype=float)

def look_at(eye, target):
    forward=np.asarray(target)-eye; forward=forward/np.linalg.norm(forward)
    right=np.cross(forward,[0.,0,1.])
    if np.linalg.norm(right)<1e-6: right=np.array([1.,0,0])
    right/=np.linalg.norm(right)
    up=np.cross(right,forward)
    return np.column_stack([right,up,-forward])

def intrinsics(width,height,hfov):
    f=width/(2*np.tan(np.deg2rad(hfov)/2))
    vfov=np.rad2deg(2*np.arctan(height/(2*f)))
    return dict(width=int(width),height=int(height),fx=float(f),fy=float(f),cx=(width-1)/2,cy=(height-1)/2,hfov=float(hfov),vfov=float(vfov))

def project(points, position, rotation, k):
    local=(np.asarray(points)-position)@rotation
    depth=-local[:,2]
    safe=np.maximum(depth,1e-9)
    uv=np.column_stack([k['fx']*local[:,0]/safe+k['cx'],k['cy']-k['fy']*local[:,1]/safe])
    return uv,depth

def unproject(pixel, depth, position, rotation, k):
    u,v=pixel
    if not np.isfinite(depth) or depth<=0: raise ValueError("Selected pixel has no valid surface depth")
    local=np.array([(u-k['cx'])*depth/k['fx'],-(v-k['cy'])*depth/k['fy'],-depth])
    return np.asarray(position)+rotation@local
