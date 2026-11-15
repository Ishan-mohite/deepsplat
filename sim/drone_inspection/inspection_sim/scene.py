from __future__ import annotations
import hashlib
import numpy as np
import trimesh
from .config import ROOT
from .coordinates import HABITAT_TO_WORLD

class SceneGeometry:
    def __init__(self, config,progress=None):
        source=config.scene_path
        if not source.is_file(): raise ValueError('Scene not found: %s. Run scripts/make_demo_scene.py or configure a local GLB.' % source)
        if source.suffix.lower()!='.glb': raise ValueError('Scene must be a GLB')
        if progress:progress('Reading scene: '+source.name)
        loaded=trimesh.load(str(source),force='scene',process=False)
        if progress:progress('Aligning scene geometry')
        self.scene=trimesh.Scene(); self.meshes=[]
        conversion=np.eye(4)
        conversion[:3,:3]=(HABITAT_TO_WORLD if config.source_up=='Y' else np.eye(3))*config.scale_to_meters
        for index,node in enumerate(loaded.graph.nodes_geometry):
            transform,name=loaded.graph[node]
            mesh=loaded.geometry[name].copy()
            if not isinstance(mesh,trimesh.Trimesh): continue
            mesh.apply_transform(conversion@transform)
            valid=np.isfinite(mesh.vertices).all(axis=1)
            if not valid.all(): raise ValueError('Scene has non-finite vertices')
            self.meshes.append(mesh); self.scene.add_geometry(mesh,node_name='part_%d'%index)
        if not self.meshes: raise ValueError('GLB contains no triangle geometry')
        # Collision and coverage only need triangles. Concatenating textured visuals
        # needlessly builds a large texture atlas for scans with many materials.
        vertices=[];faces=[];offset=0
        for mesh in self.meshes:
            vertices.append(mesh.vertices);faces.append(mesh.faces+offset);offset+=len(mesh.vertices)
        self.mesh=trimesh.Trimesh(vertices=np.concatenate(vertices),faces=np.concatenate(faces),process=False)
        self.bounds=self.mesh.bounds
        self.source_sha256=hashlib.sha256(source.read_bytes()).hexdigest()
        self.cache=ROOT/'.cache'/self.source_sha256[:16]/('%s_%s'%(config.source_up,config.scale_to_meters))
        self.cache.mkdir(parents=True,exist_ok=True)
        self.world_glb=self.cache/'world.glb'; self.habitat_glb=self.cache/'habitat.glb'
        if progress:progress('Preparing cached scene assets')
        if not self.world_glb.exists():
            self.world_glb.write_bytes(self.scene.export(file_type='glb'))
        if not self.habitat_glb.exists():
            habitat=self.scene.copy();t=np.eye(4);t[:3,:3]=HABITAT_TO_WORLD.T;habitat.apply_transform(t)
            self.habitat_glb.write_bytes(habitat.export(file_type='glb'))
        # File-backed meshes avoid PyBullet's shared-memory command size limit on scans.
        self.collision_obj=self.cache/'collision.obj'
        if not self.collision_obj.exists():
            self.mesh.export(self.collision_obj,include_texture=False,include_color=False)
        self.report=dict(source=str(source),sha256=self.source_sha256,units='meters (explicit configured scale)',
                         scale_to_meters=config.scale_to_meters,source_up=config.source_up,world='right-handed Z-up',
                         bounds=self.bounds.tolist(),triangles=len(self.mesh.faces),watertight=bool(self.mesh.is_watertight),
                         collision='all transformed triangles, static concave mesh; no simplification',
                         warning='Open scan meshes can contain holes. Verify scale, alignment and collision geometry before interpreting results.')

    def samples(self, config):
        mesh=self.mesh
        centers=mesh.triangles_center
        eligible=mesh.area_faces>1e-9
        if config.target_bounds is not None:
            bounds=np.asarray(config.target_bounds)
            eligible &= ((centers>=bounds[0]) & (centers<=bounds[1])).all(axis=1)
        indices=np.flatnonzero(eligible)
        if not len(indices): raise ValueError('The configured target bounds contain no eligible surfaces')
        areas=mesh.area_faces[indices]; total=float(areas.sum())
        rng=np.random.default_rng(config.seed)
        faces=rng.choice(indices,size=config.surface_samples,p=areas/total)
        triangle=mesh.triangles[faces]
        r1=np.sqrt(rng.random((len(faces),1)));r2=rng.random((len(faces),1))
        points=(1-r1)*triangle[:,0]+r1*(1-r2)*triangle[:,1]+r1*r2*triangle[:,2]
        return points,mesh.face_normals[faces],np.full(len(faces),total/len(faces)),faces
