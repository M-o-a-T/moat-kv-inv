=======
DistInv
=======

DistInv is a link between DistKV and your network infrastructure.

DistInv knows about hosts, ports, VLANs, (sub)networks, cables.

It stores

   * which VLAN a network is in
   * which network a host belongs to
   * which port is plugged into what
   * which groups a host belongs to
   * where a host is located

This allows you to

   * record which systems are where (surprisingly difficult …)
   * auto-generate (part of) your Ansible inventory
   * display which VLANs need to be configured on a switch port
   * generate a network link path from A to B
   * auto-update DNS entries when hosts are added, moved, or modified

