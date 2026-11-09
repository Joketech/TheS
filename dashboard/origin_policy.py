import config
"""Exact approved HTTPS origin plus existing loopback HTTP; no forwarded trust."""
import ipaddress
TLS_HOSTNAME=config.TLS_HOSTNAME
TLS_HOSTS=frozenset((TLS_HOSTNAME,TLS_HOSTNAME+':443')) if TLS_HOSTNAME else frozenset()
TLS_ORIGINS=frozenset(('https://'+TLS_HOSTNAME,'https://'+TLS_HOSTNAME+':443'))
def _values(headers,name):
 if hasattr(headers,'get_all'):return headers.get_all(name,[])
 value=headers.get(name);return [] if value is None else [value]
def allowed_origins(h):
 try:
  headers=h.headers
  if len(_values(headers,'Host'))!=1 or len(_values(headers,'Origin'))>1 or len(_values(headers,'Sec-Fetch-Site'))>1:return set()
  if not ipaddress.ip_address(h.client_address[0]).is_loopback:return set()
  if headers.get('Sec-Fetch-Site','') not in ('','none','same-origin'):return set()
  port=h.server.server_port;local={f'127.0.0.1:{port}',f'localhost:{port}'};host=headers.get('Host')
  origins={'http://'+x for x in local} if host in local else set(TLS_ORIGINS) if host in TLS_HOSTS else set()
  origin=headers.get('Origin')
  return origins if origin is None or origin in origins else set()
 except (AttributeError,ValueError,TypeError,IndexError):return set()
def trusted_request(h):return bool(allowed_origins(h))
def secure_request(h):return trusted_request(h) and h.headers.get('Host') in TLS_HOSTS
