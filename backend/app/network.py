import urllib.parse
import urllib.request


def proxy_for(url):
    target = urllib.parse.urlsplit(url)
    environment = urllib.request.getproxies_environment()
    if urllib.request.proxy_bypass_environment(target.netloc, environment):
        return None
    proxy = environment.get(target.scheme) or environment.get('all')
    if not proxy:
        registry = getattr(urllib.request, 'getproxies_registry', None)
        if registry is None or urllib.request.proxy_bypass_registry(target.netloc):
            return None
        proxy = registry().get(target.scheme)
    if not proxy:
        return None
    return proxy if '://' in proxy else 'http://' + proxy
