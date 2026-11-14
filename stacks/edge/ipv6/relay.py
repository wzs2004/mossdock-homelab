import asyncio
import ipaddress
import socket

async def pipe(reader, writer):
    while data := await reader.read(65536):
        writer.write(data)
        await writer.drain()
    if writer.can_write_eof():
        writer.write_eof()

async def relay(client_reader, client_writer):
    if not ipaddress.IPv6Address(client_writer.get_extra_info("sockname")[0]).is_global:
        client_writer.close()
        return
    upstream_writer = None
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection('127.0.0.1',18443)
        await asyncio.gather(pipe(client_reader, upstream_writer), pipe(upstream_reader, client_writer))
    except (ConnectionError, OSError):
        pass
    finally:
        client_writer.close()
        if upstream_writer is not None:
            upstream_writer.close()

async def main():
    sock = socket.socket(socket.AF_INET6)
    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('::', 443))
    server = await asyncio.start_server(relay, sock=sock)
    async with server:
        await server.serve_forever()

asyncio.run(main())
