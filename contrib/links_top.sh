#!/bin/bash

set -ex

#ip addr add {{ host.netaddr }} dev en1
#(Pdb) pp str(data['ports'][4]['port'].netaddr)
#'10.107.2.1/27'
#(Pdb) pp data['ports'][4]['port'].vlan
#None
#(Pdb) pp data['ports'][4]['port'].network
#‹Net secure_wire:10.107.2.0/27›
#(Pdb) pp data['ports'][4]['port'].network.vlan
#'secure_wire'
#data['ports'][4]['port'].netaddr[-2]

{% for n in ports %}
{%- if n.port.network is defined and n.port.network.vlan is defined %}
{%- set nn = n.port.network %}
{%- set adr = n.port.netaddr[-2] ~ "/" ~ n.port.netaddr.prefixlen %}
### {{ nn.vlan }}: {{ n.port.netaddr }}

if ! ip link ls dev {{ nn.vlan }} >/dev/null 2>&1 ; then
    ip link add link en0 name {{ nn.vlan }} type vlan id {{ nn.vlan_id }}
    ip link set {{ nn.vlan }} up
fi
if test $(ip -4 -o addr ls dev {{ nn.vlan }} | wc -l) = 0 ; then
    ip addr add {{ adr }} dev {{ nn.vlan }}
fi
{% endif %}
{%- endfor %}

### END ###

