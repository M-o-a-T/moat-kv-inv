from distkv.util import attrdict, P

CFG = attrdict(
    prefix=P(":.distkv.inventory"),
    net="192.168.0.0",
    netmask="24",
    domain="your.example",
)
