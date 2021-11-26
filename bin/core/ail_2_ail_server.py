#!/usr/bin/env python3
# -*-coding:UTF-8 -*

import json
import os
import sys
import uuid

import asyncio
import http
import ssl
import websockets

sys.path.append(os.environ['AIL_BIN'])
##################################
# Import Project packages
##################################
from pubsublogger import publisher
from core import ail_2_ail

# # TODO: refactor logging
#### LOGS ####
redis_logger = publisher
redis_logger.port = 6380
redis_logger.channel = 'AIL_SYNC_Server'

#############################

CONNECTED_CLIENT = set()
# # TODO: Store in redis

#############################

# # # # # # #
#           #
#   UTILS   #
#           #
# # # # # # #

def is_valid_uuid_v4(UUID):
    if not UUID:
        return False
    UUID = UUID.replace('-', '')
    try:
        uuid_test = uuid.UUID(hex=UUID, version=4)
        return uuid_test.hex == UUID
    except:
        return False

def unpack_path(path):
    dict_path = {}
    path = path.split('/')
    if len(path) < 3:
        raise Exception('Invalid url path')
    if not len(path[-1]):
        path = path[:-1]

    dict_path['sync_mode'] = path[1]
    dict_path['ail_uuid'] = path[-1]
    dict_path['api'] = path[2:-1]

    return dict_path

# # # # # # #


# async def send_object():
#     if CONNECTED_CLIENT:
#         message = 'new json object {"id": "test01"}'
#         await asyncio.wait([user.send(message) for user in USERS])


async def register(websocket):
    ail_uuid = websocket.ail_uuid
    remote_address = websocket.remote_address
    redis_logger.info(f'Client Connected: {ail_uuid} {remote_address}')
    print(f'Client Connected: {ail_uuid} {remote_address}')
    CONNECTED_CLIENT.add(websocket)
    #print(CONNECTED_CLIENT)

async def unregister(websocket):
    CONNECTED_CLIENT.remove(websocket)

# PULL: Send data to client
# # TODO: ADD TIMEOUT ???
async def pull(websocket, ail_uuid):

    for queue_uuid in ail_2_ail.get_ail_instance_all_sync_queue(ail_uuid):
        while True:
            # get elem to send
            Obj = ail_2_ail.get_sync_queue_object_by_queue_uuid(queue_uuid, ail_uuid, push=False)
            if Obj:
                obj_ail_stream = ail_2_ail.create_ail_stream(Obj)
                Obj = json.dumps(obj_ail_stream)
                #print(Obj)

                # send objects
                await websocket.send(Obj)
            # END PULL
            else:
                break

    # END PULL
    return None


# PUSH: receive data from client
# # TODO: optional queue_uuid
async def push(websocket, ail_uuid):
    #print(ail_uuid)
    while True:
        ail_stream = await websocket.recv()

        # # TODO: CHECK ail_stream
        ail_stream = json.loads(ail_stream)
        #print(ail_stream)

        ail_2_ail.add_ail_stream_to_sync_importer(ail_stream)

# API: server API
# # TODO: ADD TIMEOUT ???
async def api(websocket, ail_uuid, api):
    api = api[0]
    if api == 'ping':
        message = {'message':'pong'}
        message = json.dumps(message)
        await websocket.send(message)
    elif api == 'version':
        sync_version = ail_2_ail.get_sync_server_version()
        message = {'version': sync_version}
        message = json.dumps(message)
        await websocket.send(message)

    # END API
    return

async def ail_to_ail_serv(websocket, path):

    # # TODO: save in class
    ail_uuid = websocket.ail_uuid
    remote_address = websocket.remote_address
    path = unpack_path(path)
    sync_mode = path['sync_mode']

    # # TODO: check if it works
    # # DEBUG:
    # print(websocket.ail_uuid)
    # print(websocket.remote_address)
    # print(f'sync mode: {sync_mode}')

    await register(websocket)
    try:
        if sync_mode == 'pull':
            await pull(websocket, websocket.ail_uuid)
            await websocket.close()
            redis_logger.info(f'Connection closed: {ail_uuid} {remote_address}')
            print(f'Connection closed: {ail_uuid} {remote_address}')

        elif sync_mode == 'push':
            await push(websocket, websocket.ail_uuid)

        elif sync_mode == 'api':
            await api(websocket, websocket.ail_uuid, path['api'])
            await websocket.close()
            redis_logger.info(f'Connection closed: {ail_uuid} {remote_address}')
            print(f'Connection closed: {ail_uuid} {remote_address}')

    finally:
        await unregister(websocket)


###########################################
# CHECK Authorization HEADER and URL PATH #

# # TODO: check AIL UUID (optional header)

class AIL_2_AIL_Protocol(websockets.WebSocketServerProtocol):
    """AIL_2_AIL_Protocol websockets server."""

    async def process_request(self, path, request_headers):

        # DEBUG:
        # print(self.remote_address)
        # print(request_headers)

        # API TOKEN
        api_key = request_headers.get('Authorization', '')
        if api_key is None:
            redis_logger.warning(f'Missing token: {self.remote_address}')
            print(f'Missing token: {self.remote_address}')
            return http.HTTPStatus.UNAUTHORIZED, [], b"Missing token\n"

        if not ail_2_ail.is_allowed_ail_instance_key(api_key):
            redis_logger.warning(f'Invalid token: {self.remote_address}')
            print(f'Invalid token: {self.remote_address}')
            return http.HTTPStatus.UNAUTHORIZED, [], b"Invalid token\n"

        # PATH
        try:
            dict_path = unpack_path(path)
        except Exception as e:
            redis_logger.warning(f'Invalid path: {self.remote_address}')
            print(f'Invalid path: {self.remote_address}')
            return http.HTTPStatus.BAD_REQUEST, [], b"Invalid path\n"


        ail_uuid = ail_2_ail.get_ail_instance_by_key(api_key)
        if ail_uuid != dict_path['ail_uuid']:
            redis_logger.warning(f'Invalid token: {self.remote_address} {ail_uuid}')
            print(f'Invalid token: {self.remote_address} {ail_uuid}')
            return http.HTTPStatus.UNAUTHORIZED, [], b"Invalid token\n"


        if not api_key != ail_2_ail.get_ail_instance_key(api_key):
            redis_logger.warning(f'Invalid token: {self.remote_address} {ail_uuid}')
            print(f'Invalid token: {self.remote_address} {ail_uuid}')
            return http.HTTPStatus.UNAUTHORIZED, [], b"Invalid token\n"

        self.ail_key = api_key
        self.ail_uuid = ail_uuid

        if dict_path['sync_mode'] == 'pull' or dict_path['sync_mode'] == 'push':

            # QUEUE UUID
            # if dict_path['queue_uuid']:
            #
            #     if not is_valid_uuid_v4(dict_path['queue_uuid']):
            #         print('Invalid UUID')
            #         return http.HTTPStatus.BAD_REQUEST, [], b"Invalid UUID\n"
            #
            #     self.queue_uuid = dict_path['queue_uuid']
            # else:
            #     self.queue_uuid = None
            #
            # if not ail_2_ail.is_ail_instance_queue(ail_uuid, dict_path['queue_uuid']):
            #     print('UUID not found')
            #     return http.HTTPStatus.FORBIDDEN, [], b"UUID not found\n"

            # SYNC MODE
            if not ail_2_ail.is_ail_instance_sync_enabled(self.ail_uuid, sync_mode=dict_path['sync_mode']):
                sync_mode = dict_path['sync_mode']
                redis_logger.warning(f'SYNC mode disabled: {self.remote_address} {ail_uuid} {sync_mode}')
                print(f'SYNC mode disabled: {self.remote_address} {ail_uuid} {sync_mode}')
                return http.HTTPStatus.FORBIDDEN, [], b"SYNC mode disabled\n"

        # # TODO: CHECK API
        elif dict_path['sync_mode'] == 'api':
            pass

        else:
            print(f'Invalid path: {self.remote_address}')
            redis_logger.info(f'Invalid path: {self.remote_address}')
            return http.HTTPStatus.BAD_REQUEST, [], b"Invalid path\n"


###########################################

# # TODO: clean shutdown / kill all connections
# # TODO: API
# # TODO: Filter object
# # TODO: IP/uuid to block

if __name__ == '__main__':

    host = 'localhost'
    port = 4443

    print('Launching Server...')
    redis_logger.info('Launching Server...')

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    cert_dir = os.environ['AIL_FLASK']
    ssl_context.load_cert_chain(certfile=os.path.join(cert_dir, 'server.crt'), keyfile=os.path.join(cert_dir, 'server.key'))

    start_server = websockets.serve(ail_to_ail_serv, "localhost", 4443, ssl=ssl_context, create_protocol=AIL_2_AIL_Protocol)

    print(f'Server Launched:    wss://{host}:{port}')
    redis_logger.info(f'Server Launched:    wss://{host}:{port}')

    asyncio.get_event_loop().run_until_complete(start_server)
    asyncio.get_event_loop().run_forever()
