from moat.util import attrdict, P

CFG = attrdict(inv=attrdict(
    prefix=P(":.distkv.inventory"),
    net="192.168.0.0",
    netmask="24",
    domain="your.example",
))
