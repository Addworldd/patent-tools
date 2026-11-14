import json
import urllib.request
import asyncio
import websockets

# 1. 获取当前所有可控制的 target
def get_targets():
    req = urllib.request.Request("http://localhost:9222/json/list")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

def create_new_tab(url="https://analytics.zhihuiya.com/search/input/field/project"):
    req = urllib.request.Request(f"http://localhost:9222/json/new?{url}", method="PUT")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

def close_tab(tab_id):
    req = urllib.request.Request(f"http://localhost:9222/json/close/{tab_id}")
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode()

print("Targets:", len(get_targets()))
