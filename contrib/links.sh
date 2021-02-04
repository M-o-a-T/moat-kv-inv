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
## set adr = n.port.netaddr[-2] ~ "/" ~ n.port.netaddr.prefixlen 

{% for n in ports %}
{%- if n.port.network is defined and n.port.network.vlan is defined %}
{%- set nn = n.port.network %}
{%- set adr = n.port.netaddr %}
### {{ nn.vlan }}: {{ n.port.netaddr }}
{% if nn.vlan == "old" %}{% set name = "en0" %}{% else %}{% set name=nn.vlan %}{% endif %}
if ! ip link ls dev {{ name }} >/dev/null 2>&1 ; then
    ip link add link en0 name {{ name }} type vlan id {{ nn.vlan_id }}
    ip link set {{ name }} up
fi
ip addr add {{ adr }} dev {{ name }} || true
{%- if n.net6 is defined %}
ip addr add {{ n.net6 }} dev {{ name }} || true
{%- endif %}

{% endif %}
{%- endfor %}

### END ###

