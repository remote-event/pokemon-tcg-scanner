"""Check real local mDNS advertisement and the advertised health endpoint."""
import argparse
import asyncio
import json
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncZeroconf, AsyncServiceBrowser, AsyncServiceInfo

async def run(interface):
    event = asyncio.Event()
    found = []
    def update(zeroconf, service_type, name, state_change):
        if state_change is ServiceStateChange.Added:
            found.append((service_type, name))
            event.set()
    zc = AsyncZeroconf(interfaces=[interface])
    browser = AsyncServiceBrowser(zc.zeroconf, "_tcgscanner._tcp.local.", handlers=[update])
    try:
        await asyncio.wait_for(event.wait(), 15)
        info = AsyncServiceInfo(*found[0])
        assert await info.async_request(zc.zeroconf, 5000)
        address = info.parsed_addresses()[0]
        assert address == interface, (address, interface)
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"http://{address}:{info.port}/health", timeout=5) as response:
            health = json.load(response)
        assert health["ready"] and health["protocol"] == 1
        report = {"service": info.name, "address": address, "port": info.port, "health": health}
        output = Path(__file__).resolve().parents[2] / "artifacts/discovery.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2)+"\n")
        print(json.dumps(report, indent=2))
    finally:
        await browser.async_cancel()
        await zc.async_close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("interface")
    asyncio.run(run(parser.parse_args().interface))
