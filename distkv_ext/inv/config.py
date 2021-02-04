from distkv.util import attrdict

CFG = attrdict(
    prefix=(".distkv", "inventory"),
    net="192.168.0.0",
    netmask="24",
    domain="your.example",
)
