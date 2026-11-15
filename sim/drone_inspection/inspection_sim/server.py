from __future__ import annotations
import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI,Request,WebSocket,WebSocketDisconnect,HTTPException
from fastapi.responses import FileResponse,Response,StreamingResponse
from fastapi.staticfiles import StaticFiles
from .config import ROOT
from .engine import Simulation,jpeg
from .agent import AgentService


def create_app(config):
    app=FastAPI(title='DeepSplat local drone inspection')
    simulation=Simulation(config);agent=AgentService(simulation);agent_tasks=set()
    app.state.simulation=simulation
    @app.on_event('startup')
    async def startup():simulation.start()
    @app.on_event('shutdown')
    async def shutdown():
        for task in agent_tasks:task.cancel()
        await asyncio.get_running_loop().run_in_executor(None,simulation.close)
    def same_origin(origin,host):
        return not origin or urlparse(origin).netloc==host
    async def body(request):
        if not same_origin(request.headers.get('origin'),request.headers.get('host')):raise HTTPException(403,'Cross-origin control is disabled')
        if not request.headers.get('content-type','').startswith('application/json'):raise HTTPException(415,'Use application/json')
        raw=await request.body()
        if len(raw)>20000:raise HTTPException(413,'Request too large')
        try:return json.loads(raw)
        except ValueError:raise HTTPException(400,'Invalid JSON')
    @app.get('/')
    async def index():return FileResponse(ROOT/'web/index.html')
    @app.get('/api/state')
    async def state():return simulation.read()
    @app.post('/api/command')
    async def command(request:Request):
        value=await body(request)
        if not isinstance(value,dict):raise HTTPException(400,'Expected a structured command')
        if not simulation.read().get('ready'):raise HTTPException(409,'Simulation is not ready')
        try:simulation.submit(value)
        except ValueError as error:raise HTTPException(400,str(error))
        return {'accepted':True,'note':'Execution result is reported in streamed state'}
    @app.post('/api/chat')
    async def chat(request:Request):
        value=await body(request)
        if agent.pending>=2:raise HTTPException(429,'Agent requests pending')
        text=str(value.get('message','')).strip();instructions=str(value.get('instructions',''))
        if not text:raise HTTPException(400,'Message is empty')
        task=asyncio.create_task(agent.request(text,instructions));agent_tasks.add(task);task.add_done_callback(agent_tasks.discard)
        return {'accepted':True}
    @app.get('/api/frames/{frame_id}.jpg')
    async def frame(frame_id:str):
        with simulation.frame_lock:observation=simulation.frames.get(frame_id)
        if observation is None:raise HTTPException(404,'Frame expired')
        return Response(jpeg(observation.rgb),media_type='image/jpeg',headers={'Cache-Control':'no-store'})
    @app.get('/api/map.glb')
    async def scene():
        if simulation.geometry is None:raise HTTPException(409,'Scene is loading')
        return FileResponse(simulation.geometry.world_glb,media_type='model/gltf-binary')
    @app.get('/api/stream/{view}.mjpg')
    async def stream(view:str):
        if view not in ('first','third'):raise HTTPException(404,'Unknown view')
        async def images():
            previous=None
            while True:
                with simulation.lock:data=simulation.images.get(view)
                if data and data is not previous:
                    yield b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(data)).encode()+b'\r\n\r\n'+data+b'\r\n';previous=data
                await asyncio.sleep(.05)
        return StreamingResponse(images(),media_type='multipart/x-mixed-replace; boundary=frame',headers={'Cache-Control':'no-store'})
    @app.websocket('/api/events')
    async def events(socket:WebSocket):
        if not same_origin(socket.headers.get('origin'),socket.headers.get('host')):await socket.close(code=1008);return
        await socket.accept()
        async def receive():
            while True:
                value=await socket.receive_json()
                if value=={'type':'heartbeat'}:simulation.submit(value)
        receiver=asyncio.create_task(receive())
        try:
            while not receiver.done():
                await socket.send_json(simulation.read());await asyncio.sleep(.2)
        except (WebSocketDisconnect,RuntimeError):pass
        finally:receiver.cancel()
    app.mount('/web',StaticFiles(directory=str(ROOT/'web')),name='web')
    app.mount('/design',StaticFiles(directory=str(ROOT.parents[1]/'design')),name='design')
    three=ROOT/'node_modules/three'
    if three.exists():app.mount('/vendor/three',StaticFiles(directory=str(three)),name='three')
    return app
