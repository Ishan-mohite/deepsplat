from __future__ import annotations
import heapq
import itertools
import math
import hashlib
import json
import numpy as np
from scipy.spatial.transform import Rotation
from .coordinates import camera_pose

class OccupancyGrid:
    def __init__(self,geometry,physics,config,progress=None):
        self.step=config.grid_resolution;self.origin=geometry.bounds[0]
        self.shape=np.ceil((geometry.bounds[1]-self.origin)/self.step).astype(int)+1
        if np.prod(self.shape)>config.max_grid_cells: raise ValueError('Scene exceeds max_grid_cells. Crop geometry or increase grid_resolution explicitly.')
        self.blocked=np.ones(tuple(self.shape),dtype=bool);self.physics=physics
        signature=json.dumps([1,self.step,physics.radius,config.clearance,self.origin.tolist(),self.shape.tolist()])
        cache=geometry.cache/('occupancy_'+hashlib.sha256(signature.encode()).hexdigest()[:16]+'.npy')
        if cache.exists():
            try:
                blocked=np.load(cache,allow_pickle=False)
                if blocked.shape==self.blocked.shape and blocked.dtype==np.bool_:
                    self.blocked=blocked
                    if progress:progress(self.blocked.size,self.blocked.size)
                    return
            except (ValueError,OSError):pass
        total=self.blocked.size
        for count,index in enumerate(np.ndindex(*self.shape),1):
            self.blocked[index]=not physics.is_free(self.point(index))
            if progress and (count%256==0 or count==total):progress(count,total)
        temporary=cache.with_suffix('.tmp')
        with temporary.open('wb') as output:np.save(output,self.blocked,allow_pickle=False)
        temporary.replace(cache)
    def point(self,index): return self.origin+np.asarray(index)*self.step
    def index(self,point): return tuple(np.rint((np.asarray(point)-self.origin)/self.step).astype(int))
    def valid(self,index): return all(0<=i<n for i,n in zip(index,self.shape)) and not self.blocked[index]
    def validate_start(self,position):
        cell=self.index(position)
        if not self.valid(cell) or not self.physics.segment_clear(position,self.point(cell)):
            raise ValueError('Starting pose cannot reach its navigation grid cell. Choose a free cell center away from surfaces.')
        exits=[]
        for axis in range(3):
            for sign in (-1,1):
                neighbor=list(cell);neighbor[axis]+=sign;neighbor=tuple(neighbor)
                if self.valid(neighbor) and self.physics.segment_clear(position,self.point(neighbor)):
                    exits.append(neighbor)
        if not exits:
            raise ValueError('Starting pose is trapped: no collision-free exit to a neighboring navigation cell.')
        return {'position':np.asarray(position,dtype=float).tolist(),'grid_cell':[int(i) for i in cell],'reachable_neighbors':len(exits)}
    def path(self,start,end):
        a,b=self.index(start),self.index(end)
        if not self.valid(a) or not self.valid(b): return None
        if not self.physics.segment_clear(start,self.point(a)) or not self.physics.segment_clear(end,self.point(b)): return None
        queue=[(0.,a)];cost={a:0.};parent={}
        directions=list(itertools.product([-1,0,1],repeat=3));directions.remove((0,0,0))
        while queue:
            _,node=heapq.heappop(queue)
            if node==b:
                route=[np.asarray(end)]
                while node!=a: route.append(self.point(node));node=parent[node]
                route.append(np.asarray(start));route.reverse()
                # Remove only waypoints whose inflated straight segment is collision-free.
                simplified=[route[0]];current=0
                while current<len(route)-1:
                    next_index=len(route)-1
                    while next_index>current+1 and not self.physics.segment_clear(route[current],route[next_index]): next_index-=1
                    simplified.append(route[next_index]);current=next_index
                return simplified
            for direction in directions:
                neighbor=tuple(x+d for x,d in zip(node,direction))
                if not self.valid(neighbor): continue
                # Prevent diagonal corner cutting through any adjacent occupied cell.
                axes=[i for i,d in enumerate(direction) if d]
                safe=True
                for length in range(1,len(axes)+1):
                    for subset in itertools.combinations(axes,length):
                        intermediate=list(node)
                        for axis in subset: intermediate[axis]+=direction[axis]
                        if not self.valid(tuple(intermediate)):safe=False;break
                    if not safe:break
                if not safe:continue
                value=cost[node]+float(np.linalg.norm(direction))
                if value<cost.get(neighbor,float('inf')):
                    cost[neighbor]=value;parent[neighbor]=node
                    heapq.heappush(queue,(value+np.linalg.norm(np.asarray(neighbor)-b),neighbor))
        return None

class InspectionPlanner:
    def __init__(self,coverage,grid,physics,config):
        self.coverage=coverage;self.grid=grid;self.physics=physics;self.config=config
        self.visits={};self.decisions=0
    def next_view(self,current,k):
        pending=np.flatnonzero(self.coverage.target & ~self.coverage.covered)
        if not len(pending):return None
        # Bounded, deterministic candidate search; unobservable means under these constraints, not a proof of impossibility.
        indices=pending[np.linspace(0,len(pending)-1,min(40,len(pending))).astype(int)]
        candidates={}
        for index in indices:
            point=self.coverage.points[index];normal=self.coverage.normals[index]
            tangent=np.cross(normal,[0.,0,1.])
            if np.linalg.norm(tangent)<.1:tangent=np.array([1.,0,0])
            tangent/=np.linalg.norm(tangent);other=np.cross(normal,tangent)
            for distance in [0.8,1.5,2.2]:
                for offset in [np.zeros(3),tangent*1.2,-tangent*1.2,other*.8,-other*.8]:
                    position=point+normal*distance+offset
                    cell=self.grid.index(position)
                    if not self.grid.valid(cell):continue
                    position=self.grid.point(cell)
                    yaw=math.atan2(point[1]-position[1],point[0]-position[0])
                    key=cell+(int(round(yaw/.4)),)
                    if self.visits.get(key,0)>=2:continue
                    eye,rotation=camera_pose(position,Rotation.from_euler('z',yaw).as_quat())
                    mask=self.coverage.geometric_candidates(eye,rotation,k)&~self.coverage.covered
                    gain=float(self.coverage.areas[mask].sum())
                    if gain>0:
                        score=gain/(.5+np.linalg.norm(position-current))
                        candidates[key]=(score,position,yaw,key,eye,rotation,point)
        for _,position,yaw,key,eye,rotation,target in sorted(candidates.values(),key=lambda c:c[0],reverse=True)[:30]:
            visible=self.coverage.visible_mask(eye,rotation,k,self.physics.visible)&~self.coverage.covered
            if not visible.any():continue
            route=self.grid.path(current,position)
            if route:
                self.visits[key]=self.visits.get(key,0)+1;self.decisions+=1
                return dict(position=position,yaw=yaw,route=route[1:],target=target,expected_area=float(self.coverage.areas[visible].sum()))
        return None
