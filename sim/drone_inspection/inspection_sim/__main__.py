import argparse
import uvicorn
from .config import Config
from .server import create_app

def main():
    parser=argparse.ArgumentParser(description='Local indoor drone inspection prototype')
    parser.add_argument('--config');parser.add_argument('--renderer',choices=['habitat','bullet'])
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args();config=Config.load(args.config)
    if args.renderer:config.renderer=args.renderer
    # Keep the first version local; no authentication/public hosting is implied.
    uvicorn.run(create_app(config),host='127.0.0.1',port=args.port)
if __name__=='__main__':main()
