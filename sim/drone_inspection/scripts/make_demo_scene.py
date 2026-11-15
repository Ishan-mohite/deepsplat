"""Create an original metric GLB demo room; no HM3D/MP3D data is redistributed."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import trimesh
from inspection_sim.config import ROOT
from inspection_sim.coordinates import HABITAT_TO_WORLD

def main():
    scene=trimesh.Scene()
    boxes=[([6.4,6.4,.2],[0,0,-.1],[105,110,124,255]),
           ([6.4,6.4,.2],[0,0,3.1],[195,199,211,255]),
           ([.2,6.4,3.2],[-3.1,0,1.5],[160,171,194,255]),
           ([.2,6.4,3.2],[3.1,0,1.5],[176,164,191,255]),
           ([6.4,.2,3.2],[0,-3.1,1.5],[175,185,202,255]),
           ([6.4,.2,3.2],[0,3.1,1.5],[170,180,199,255]),
           ([.65,.65,1.15],[1.2,1.0,.575],[198,131,86,255])]
    for i,(extents,position,color) in enumerate(boxes):
        mesh=trimesh.creation.box(extents=extents);mesh.apply_translation(position);mesh.visual.face_colors=color
        transform=np.eye(4);transform[:3,:3]=HABITAT_TO_WORLD.T;mesh.apply_transform(transform)
        scene.add_geometry(mesh,node_name='fixture_'+str(i))
    path=ROOT/'assets/scenes/demo_room.glb';path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(scene.export(file_type='glb'));print(path)
if __name__=='__main__':main()
