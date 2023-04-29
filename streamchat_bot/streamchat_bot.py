import asyncio
import json
import urllib
from datetime import datetime

import aiohttp


class StreamChatBot:
    def __init__(self, api_key, user, user_token, on_message):
        self.api_key = api_key
        self.user = user
        self.user_token = user_token
        self.on_message = on_message
        self.base_url = 'https://chat.stream-io-api.com'
        self.ws_base_url = self.base_url.replace('http', 'ws')
        self.last_event = None
        self.connection_id = None
        self.shutdown = False

    async def _check_heartbeat(self):
        """
        check every 40 seconds if we have received a message in the last 40 seconds, if not so we shut down the bot
        """
        while True:
            if self.shutdown:
                break
            if self.last_event and (datetime.now() - self.last_event).seconds > 40:
                self.shutdown = True
                raise Exception('No heartbeat in last 40 seconds')
            await asyncio.sleep(40)

    async def _send_heartbeat(self):
        """
        send a heartbeat every 30 seconds
        """
        while True:
            if self.shutdown:
                break
            if self.ws:
                await self.ws.send_json({"type": "health.check", "connection_id": self.connection_id})
            await asyncio.sleep(20)

    async def run(self):
        try:
            user_details = {
                "id": self.user,
                "name": self.user,
            }
            json_data = {
                "user_id": self.user,
                "user_details": user_details
            }
            json_param = urllib.parse.quote(json.dumps(json_data))
            ws_url = f"{self.ws_base_url}/connect?json={json_param}&api_key={self.api_key}&authorization={self.user_token}&stream-auth-type=jwt&X-Stream-Client=stream-chat-python-client-0.1"
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(ws_url) as ws:
                    self.ws = ws
                    async for msg in ws:
                        if self.shutdown:
                            break
                        self.last_event = datetime.now()
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            if data['type'] == 'health.check':
                                self.connection_id = data['connection_id']
                                break
                    tasks = [
                        asyncio.create_task(self._check_heartbeat()),
                        asyncio.create_task(self._send_heartbeat()),
                        asyncio.create_task(self._message_loop())
                    ]
                    await asyncio.gather(*tasks)
        except Exception as e:
            print(e)
            self.shutdown = True

    async def _message_loop(self):
        await self._query_channels()
        async for msg in self.ws:
            if self.shutdown:
                break
            self.last_event = datetime.now()
            if msg.type == aiohttp.WSMsgType.ERROR:
                raise Exception('Websocket connection closed with exception')
            data = json.loads(msg.data)
            if data["type"] == "message.new":
                if data["user"]["id"] == self.user:
                    continue
                channel = {"id": data["channel_id"], "type": data["channel_type"], "cid": data["cid"]}
                message = data["message"]
                await self.on_message(self, channel, message)
            elif data["type"] == "notification.added_to_channel":
                await self._query_channel(data["channel"])

    async def _post(self, url, data):
        params = {
            "api_key": self.api_key,
            "connection_id": self.connection_id,
            "user_id": self.user
        }
        headers = {
            "Authorization": self.user_token,
            "stream-auth-type": "jwt",
            "X-Stream-Client": "stream-chat-python-client-0.1",
            "Content-Type": "application/json"
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, params=params, headers=headers, data=json.dumps(data)) as response:
                if response.ok:
                    return await response.json()
                else:
                    raise Exception(await response.text())

    async def send_message(self, channel, message):
        url = f'{self.base_url}/channels/{channel["type"]}/{channel["id"]}/message'
        data = {
            "message": {
                "text": message
            }
        }
        return await self._post(url, data)

    async def _query_channels(self):
        url = f'{self.base_url}/channels'
        data = {
            "filter_conditions": {
                "type": "messaging",
                "members": {
                    "$in": [self.user]
                }
            },
            "sort": [
                {"field": "last_message_at", "direction": -1},
                {"field": "updated_at", "direction": -1}
            ],
            "state": True,
            "watch": True,
            "presence": True,
            "limit": 30,
            "offset": 0
        }
        return await self._post(url, data)

    async def _query_channel(self, channel):
        url = f'https://chat.stream-io-api.com/channels/{channel["type"]}/{channel["id"]}/query'
        data = {"data": {}, "state": True, "watch": True, "presence": False}
        return await self._post(url, data)

    def close(self):
        self.shutdown = True
