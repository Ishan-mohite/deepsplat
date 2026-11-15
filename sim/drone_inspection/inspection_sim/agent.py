"""Agent requests are asynchronous proposals. Only validated normal UI commands may execute."""
from __future__ import annotations
import asyncio
import json
import os
import time
import uuid
from urllib.request import Request,urlopen

ALLOWED={'hover','pause','resume','cancel','start','velocity','save'}

def validate_proposal(proposal):
    if not isinstance(proposal,dict) or set(proposal)-{'explanation','command'}:raise ValueError('Agent must return explanation and one structured command')
    command=proposal.get('command')
    if not isinstance(command,dict) or command.get('type') not in ALLOWED:raise ValueError('Agent tool is not allowed')
    if command['type']=='velocity':
        import numpy as np
        value=np.asarray(command.get('value'),dtype=float)
        if value.shape!=(4,) or not np.isfinite(value).all():raise ValueError('Invalid agent velocity')
        if command.get('frame','body_horizontal_world_vertical') not in ('world','body_horizontal_world_vertical'):raise ValueError('Invalid agent command frame')
    if command['type']=='start' and command.get('mode','autonomous') not in ('autonomous','user-directed'):raise ValueError('Invalid task mode')
    forbidden=set(command)-{'type','value','frame','mode','task'}
    if forbidden:raise ValueError('Agent cannot change safety settings or physics state directly')
    return {'explanation':str(proposal.get('explanation',''))[:500],'command':command}

class DeterministicAgent:
    async def propose(self,text,instructions,state):
        normalized=text.lower().strip()
        # Explicit presets, not fabricated language understanding or semantic object labels.
        if normalized in ('hover','pause','resume','cancel','save'):
            return {'explanation':'Applying the %s control through the shared command path.'%normalized,'command':{'type':normalized}}
        if normalized in ('inspect all','inspect all surfaces','autonomous'):
            return {'explanation':'Inspecting all eligible surfaces using bounded collision-free viewpoints.','command':{'type':'start','mode':'autonomous','task':text}}
        if normalized in ('inspect selection','inspect region','user-directed'):
            return {'explanation':'Inspecting the previewed 3D region using the same planner and limits.','command':{'type':'start','mode':'user-directed','task':text}}
        raise ValueError('Deterministic agent supports: inspect all, inspect selection, hover, pause, resume, cancel, save. Configure an optional JSON agent provider for language-based proposals.')

class RemoteJSONAgent:
    """Optional provider gateway; no external request occurs unless explicitly configured via environment."""
    async def propose(self,text,instructions,state):
        endpoint=os.environ['INSPECTION_AGENT_URL']
        if not endpoint.startswith('https://'):raise ValueError('Remote agent endpoint must use HTTPS')
        safe_state={key:state.get(key) for key in ('state','mode','position','coverage','selection','config')}
        payload=json.dumps({'instructions':instructions,'message':text,'state':safe_state,
            'schema':{'explanation':'string','command':{'type':sorted(ALLOWED)}},
            'constraints':'Return JSON only. You cannot disable safety checks, change scene geometry or alter limits.'}).encode()
        token=os.environ.get('INSPECTION_AGENT_TOKEN','')
        def fetch():
            request=Request(endpoint,data=payload,headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
            with urlopen(request,timeout=30) as response:return json.loads(response.read(100000))
        return await asyncio.get_running_loop().run_in_executor(None,fetch)

class AgentService:
    def __init__(self,simulation):self.sim=simulation;self.pending=0
    async def request(self,text,instructions):
        if self.pending>=2:raise ValueError('Two agent requests are already pending')
        self.pending+=1;id=uuid.uuid4().hex
        entry={'id':id,'user':text[:2000],'status':'pending','explanation':'Waiting for agent proposal'}
        with self.sim.lock:self.sim.chat.append(entry)
        try:
            provider=RemoteJSONAgent() if os.environ.get('INSPECTION_AGENT_URL') else DeterministicAgent()
            proposal=validate_proposal(await provider.propose(text[:2000],instructions[:4000],self.sim.read()))
            self.sim.submit(proposal['command'])
            with self.sim.lock:entry.update(status='submitted',explanation=proposal['explanation'])
        except Exception as error:
            with self.sim.lock:entry.update(status='rejected',explanation=str(error))
        finally:self.pending-=1
