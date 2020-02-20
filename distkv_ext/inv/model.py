"""
DistKV client data model for Inventory
"""
import anyio
from anyio.exceptions import ClosedResourceError
import struct

from distkv.obj import ClientEntry, ClientRoot, AttrClientEntry
from distkv.util import combine_dict, NotGiven, attrdict
from distkv.errors import ErrorRoot
from collections.abc import Mapping
from operator import attrgetter
from weakref import ref, WeakSet, WeakValueDictionary

from typing import Union

from netaddr import IPNetwork, EUI
import logging
logger = logging.getLogger("distkv_ext.inv.model")
        

class NamedMixin:
    def __init__(self,*a,**k):
        self.__named = {}
        super().__init__(*a,**k)

    def by_name(self, name):
        if name is None:
            return None
        if not isinstance(name,str):
            raise ValueError("No string: "+repr(name))
        return self.__named.get(name)

    def _add_name(self, obj):
        n = obj.name
        if n is None:
            return

        self.__named[n] = obj
        obj.reg_del(self,'_del__name',obj,n)
        
    def _del__name(self, obj, n):
        old = self.__named.pop(n)
        if old is None or old is obj:
            return
        # Oops, that has been superseded. Put it back.
        self.__named[n] = old
        
class SkipNone:
    def get_value(self, **kw):
        kw['skip_none'] = True
        kw['skip_empty'] = True
        return super().get_value(**kw)

class Cleaner:
    def __init__(self, *a,**k):
        self._cleaner = []
        super().__init__(*a,**k)

    def reg_del(self, p, m, o, *a, **k):
        self._cleaner.append((ref(p),m, ref(o), a,k))

    async def set_value(self, value=NotGiven):
        d,self._cleaner = self._cleaner,[]
        for p,m,o,a,k in d:
            p = p()
            if p is None:
                continue
            attrgetter(m)(p)(o(),*a,**k)
        await super().set_value(value)

class InventoryRoot(ClientRoot):
    cls = {}
    reg = {}
    CFG = "inv"
    err = None

    async def run_starting(self):
        self.host = self.follow("host", create=True)
        self.net = self.follow("net", create=True)
        self.cable = self.follow("cable", create=True)
        self.group = self.follow("group", create=True)
        self.vlan = self.follow("vlan", create=True)
        self.wire = self.follow("wire", create=True)

        if self.err is None:
            self.err = await ErrorRoot.as_handler(self.client)

        await super().run_starting()

    async def running(self):
        self.cable._running()
        await super().running()

    @classmethod
    def register(cls, typ):
        def acc(kls):
            cls.reg[typ] = kls
            return kls
        return acc

    @classmethod
    def child_type(cls, name):
        try:
            return cls.reg[name]
        except KeyError:
            return ClientEntry

    def cable_for(self, *a,**k):
        """\
            Return the cable for this node+port
            """
        return self.cable.cable_for(*a, **k)



class Vlan(Cleaner, SkipNone, AttrClientEntry):
    """\
        A single VLAN.

        Stored as ``inv vlan NUMBER``.
        """
    ATTRS = ('desc','name')
    name = None
    desc = None

    def __init__(self,*a,**k):
        self._nets = {}
        super().__init__(*a,**k)
    
    def _add_net(self,obj):
        n = obj.name
        if n is None:
            return
        self._nets[n]=obj
        obj.reg_del(self,'_del__net',obj,n)

    def _del__net(self,obj,n):
        old = self._nets.pop(n)
        if old is None or old is obj:
            return
        # Oops, that has been superseded. Put it back.
        self._named[n] = old

    @property
    def vlan(self):
        return self._path[-1]

    async def set_value(self, value=NotGiven):
        """Called by the network to update my value."""
        await super().set_value(value)

        self.parent._add_name(self)

        for n2 in self.root.net:
            for n in n2:
                if n.vlan == self.name:
                    self._add_net(n)

    @property
    def networks(self):
        """Return the list of networks in this VLAN"""
        return self._nets.values()

    def __repr__(self):
        return "‹VLAN %s:%d›" % (self.name,self.vlan)

    def __str__(self):
        return "%s~%d" % (self.name,self.vlan)


@InventoryRoot.register("vlan")
class VlanRoot(NamedMixin, ClientEntry):
    """\
        Manage VLANs.
        """
    def child_type(cls, name):
        return Vlan

    def by_name(self, name):
        if isinstance(name, bool):
            return name
        return super().by_name(name)

    async def update(self, value, _locked=False):
        raise ValueError("No values here!")


class Network(Cleaner, SkipNone, AttrClientEntry):
    """\
        Manage a network.
        The network itself is stored as a (bits,netnum) in the path.

        desc: some description
        name: this network's name
        vlan: the VLAN's name this net belongs to
        dhcp: (start,len) tuple for DHCP-allocated addresses
        super: this net co-exists with another net
               usually this is ipv6 and the supernet is ipv4
        shift: the number of bits to shift that net's host's net number to
               the left
        mac: ipv6: flag whether to use a host's MAC address
             True:yes,exclusively False:no None:both

        Stored as ``inv net NETBITS NETNUMBER``.
        """
    ATTRS = ('desc','name','vlan','dhcp','master','shift')
    AUX_ATTRS=('net',)
    desc=None
    name=None
    vlan=None
    shift=0
    dhcp=(1,0)
    master=None
    _slaves=WeakSet()
    _next_adr = 2
    _master=None
    _name=None

    def __init__(self,*a,**kw):
        self._hosts = {}
        super().__init__(*a,**kw)

    @property
    def net(self):
        return IPNetwork((self._path[-1],self.prefix))
    
    @property
    def max(self):
        p = self.prefix
        if p <= 32:
            p = 32-p
        else:
            p = 128-p
        return (1<<p)-1

    def __eq__(self,net):
        if isinstance(net,Network):
            net = net._name
        return self.name == net._name

    def __hash__(self):
        return hash(self._name)

    @property
    def prefix(self):
        return self._path[-2]

    def addr(self, num=0):
        n = self.net
        n.value += num<<self.shift # sigh, should be a method
        return n

    def addrs(self,num=0):
        for n in self.all_nets:
            yield n.addr(num)

    @property
    def all_nets(self):
        yield self
        for s in self._slaves:
            yield from s.all_nets

    def _add_slave(self,net):
        if self in net.all_nets:
            return # cycle. Ugh.
        self._slaves.add(net)
        net.reg_del(self,'_del__slave',net)

    def _del__slave(self,net):
        if net is None:
            return
        self._slaves.remove(net)

    def alloc(self):
        """\
            Return the next free host number
            """
        last = self._next_adr
        t = last
        while True:
            n = t+1
            if n >= self.max:
                n = 2
            if self.dhcp:
                a = self.dhcp[0]
                b = a+self.dhcp[1]
                if a <= n < b:
                    n = b
            if n >= self.max:
                n = 2
            if t not in self._hosts:
                self._next_adr = n
                return t
            t = n
            if t == last:
                return None

    async def set_value(self, value=NotGiven):
        """\
            Called by the network to update my value.
            """
        await super().set_value(value)

        self.parent.parent._add_name(self)

        if self.master:
            n = self.root.net.by_name(self.master)
            if n:
                n._add_subnet(self)

        if self.vlan is not None:
            v = self.root.vlan.by_name(self.vlan)
            if v is not None:
                v._add_net(self)

        for h in self.root.host.all_children:
            if h.net == self.name:
                self._add_host(h)

    async def save(self, *, wait=False):
        if self.name is not None and self.root.net.by_name(self.name) not in (self,None):
            raise KeyError("Duplicate name",self.name)
        if self.vlan is not None and self.root.vlan.by_name(self.vlan) is None:
            raise KeyError("Unknown VLAN",self.vlan)
        if self.master is not None and self.root.net.by_name(self.master) is None:
            raise KeyError("Unknown master",self.vlan)
        if self.dhcp is not None:
            a,b = self.dhcp
            # start,len. Thus start+len-1 is the top address and must be
            # <= the broadcast addr.
            if a < 2 or a+b > self.max:
                raise RuntimeError("Check DHCP params",self.dhcp)
        await super().save(wait=wait)

    @property
    def hosts(self):
        return self._hosts.values()

    def _add_host(self, host):
        if not host.num:
            return
        self._hosts[host.num] = host
        host.reg_del(self,'_del__host',host,host.num)

    def _del__host(self,host,n):
        old = self._hosts.pop(n)
        if old is None or old is host:
            return
        # Oops, that has been superseded. Put it back.
        self._hosts[n] = old

    def __repr__(self):
        return "‹Net %s:%s›" % (self.name,self.net)

    def __str__(self):
        j={}
        j['vlan']=self.vlan
        if self.dhcp:
            j['dhcp'] = "%d-%d" % (self.dhcp[0],self.dhcp[0]+self.dhcp[1]-1)
        return "%s %s %s" % (self.name,self.net, " ".join("%s=%s" % (k,v)
            for k,v in j.items() if v))


class NetRootB(ClientEntry):
    """\
        Cables are unnamed and stored as (node,tock) tuple.
        """
    def child_type(cls, name):
        if not isinstance(name,int):
            return ClientEntry
        return Network

    async def update(self, value, _locked=False):
        raise ValueError("No values here!")


@InventoryRoot.register("net")
class NetRoot(NamedMixin, ClientEntry):
    """\
        Manage networks.
        """
    def __init__(self,*a,**k):
        self._nets = {}
        super().__init__(*a,**k)

    def child_type(cls, name):
        if not isinstance(name,int):
            return ClientEntry
        return NetRootB

    def by_name(self, net):
        n = super().by_name(net)
        if n is not None:
            return n
        try:
            n = IPNetwork(net)
        except Exception:
            return None
        else:
            return self.get(n)

    def enclosing(self, net):
        """find the network containing this address"""
        num = net.cidr.value
        bits = net.prefixlen
        mx=32 if bits<=32 else 128
        if not bits:
            raise KeyError(net)
        while True:
            bits -= 1
            try:
                num &= ~((1<<(mx-bits))-1)
                return self[bits][num]
            except KeyError:
                if not bits:
                    raise KeyError(net) from None
                bits -= 1

    def allocate(self, net):
        if not isinstance(net,IPNetwork):
            return super().allocate(net)
        n = super().allocate(net.prefixlen, exists=True)
        return n.allocate(net.cidr.value)

    def get(self, net):
        if not isinstance(net,IPNetwork):
            return super().get(net)
        n = super().get(net.prefixlen)
        if n is None:
            return None
        return n.get(net.network.value)

    def __getitem__(self, net):
        if not isinstance(net,IPNetwork):
            return super().__getitem__(net)
        return super().__getitem__(net.prefixlen)[net.network.value]

    async def update(self, value, _locked=False):
        raise ValueError("No values here!")


class HostPort:
    ATTRS=('desc','mac')
    ATTRS2=('net','num')
    AUX_ATTRS=('netaddr','vlan','link_to')
    desc=None
    vlan=None
    net=None
    num=None
    mac=None

    def __init__(self, host,name,kv):
        self.host=host
        self.name=name

        for a in self.ATTRS+self.ATTRS2:
            setattr(self,a,kv.pop(a,None))

        m = self.mac
        if m is not None:
            m = struct.unpack('>%dH'%(len(m)/2), m)
            mm = 0
            for m_ in m:
                mm = (mm<<16)+m_
            self.mac = EUI(mm,len(m)*16)

        self.attrs = attrdict(kv)

    @property
    def netaddr(self):
        if self.num is None:
            return None
        return self.network.addr(self.num)

    @property
    def network(self):
        return self.host.root.net.by_name(self.net)

    @property
    def vlan(self):
        vlan = self.attrs.get('vlan',None)
        if isinstance(vlan,bool):
            return vlan
        if vlan is None:
            return None
        return self.host.root.vlan.by_name(vlan)

    @vlan.setter
    def vlan(self, vlan: Union[None,bool,str,Vlan]):
        if vlan is not None:
            if isinstance(vlan,bool):
                pass
            elif not isinstance(vlan,str):
                vlan = vlan.name
            elif self.host.root.vlan.by_name(vlan) is None:
                raise ValueError("VLAN '%s' does not exist" % (vlan,))
            self.attrs.vlan = vlan
        else:
            self.attrs.pop('vlan',None)

    async def rename(self, name):
        h = self.host
        if name in h.port:
            raise KeyError("Port exists".name)

        c = self.link_to
        del h.port[self.name]
        h.port[name] = self
        self.name = name

        # Need to strictly sequence these changes
        if c is not None:
            await c.unlink(wait=True)
        await h.save(wait=True)
        if c is not None:
            await self.link(c, wait=True)

    async def link(self,other, wait=True):
        await self.host.root.cable.link(self, other, wait=wait)

    async def unlink(self):
        await self.host.root.cable.unlink(self)

    @vlan.deleter
    def vlan(self):
        self.attrs.pop('vlan',None)

    @property
    def cable(self):
        return self.host.root.cable.cable_for(self)

    @property
    def link_to(self):
        c = self.cable
        if c is None:
            return None
        return c.other_end(self)

    def get_value(self):
        res = dict(self.attrs)
        for k in self.ATTRS+self.ATTRS2:
            v = getattr(self,k,None)
            if v is not None:
                res[k] = v
        if 'mac' in res:
            res['mac'] = res['mac'].packed
        return res
        
    def __repr__(self):
        return "<Port %s:%s>" % (self.host.name,self.name)

    def __str__(self):
        return "%s:%s" % (self.host.name,self.name)


class Host(Cleaner, SkipNone, AttrClientEntry):
    ATTRS = ('name','net','num','desc','loc','groups','cable')
    AUX_ATTRS=('ports','domain','cable')
    net=None
    ports=()
    name=None
    num=None
    desc=None
    loc=None
    groups=()

    def __init__(self, *a,**k):
        self._ports = {}
        super().__init__(*a,**k)

    def child_type(cls, name):
        return Host

    @property
    def _hostroot(self):
        return self.parent._hostroot

    @property
    def network(self):
        return self.root.net.by_name(self.net)

    @property
    def cable(self):
        c = self.root.cable.cable_for(self)
        if c is not None:
            c = c.other_end(self)
        return c

    @property
    def netaddr(self):
        if self.num is None:
            return None
        n = self.network
        if n is None:
            return None
        n = n.net
        n.value += self.num # sigh, should be a method
        return n

    @property
    def netaddrs(self):
        yield from self.network.addrs(self.num)
        for n in self.nets:
            yield self.root.net.net(n).net[self.num]

    @property
    def port(self):
        return self._ports

    @property
    def ports(self):
        r = {}
        for k,v in self._ports.items():
            vv = []
            c = self.root.cable.cable_for(v)
            if c is not None:
                vv.append(c.other_end(self))
            if v.vlan:
                vv.append(v.vlan)
            r[k] = vv
        return r

    def add_port(self,name, **kw):
        if name in self._ports:
            raise KeyError("Duplicate port",name)
        p = HostPort(self,name, kw)
        self._ports[name]=p
        return p

    async def delete_port(self, port, *, wait=False):
        c = self.root.cable.cable_for(port)
        if c is not None:
            await c.delete()
        del self._ports[port.name]
        await self.save(wait=wait)

    async def delete(self, *, wait=False):
        for port in list(self._ports.values()):
            c = self.root.cable.cable_for(port)
            if c is not None:
                await c.delete(wait=wait)
        await super().delete(wait=wait)

    async def set_value(self, value=NotGiven):
        """\
            Called by the network to update my value.
            """
        if value is NotGiven:
            ports = {}
        else:
            ports = value.pop('ports',{})

        await super().set_value(value)

        if value is NotGiven:
            self._ports = {}
            return

        for k in [ k for k in self._ports.keys() if k not in ports ]:
            del self._ports[k]
        for k,v in ports.items():
            self._ports[k] = HostPort(self,k,v)

        self._hostroot._add_name(self)
        n = self.network
        if n is not None and self.num is not None:
            n._add_host(self)
        for p in self.port.values():
            n = p.network
            if n is not None and p.num is not None:
                n._add_host(self)

    async def link(self,other, *, wait=True):
        await self.root.cable.link(self, other, wait=wait)

    async def unlink(self, *, wait=False):
        await self.root.cable.unlink(self, wait=wait)


    def get_value(self):
        if self.name is not None and self.root.net.by_name(self.name) not in (self,None):
            raise KeyError("Duplicate name",self.name)

        val = super().get_value()
        val['ports'] = p = {}
        for k,v in self._ports.items():
            p[k] = v.get_value()
        return val

    @property
    def domain(self):
        return ".".join(self.subpath[-1:0:-1])

    def __repr__(self):
        return "<Host %s: %s %s>" % (self.name, self.domain, self.netaddr)

    def __str__(self):
        j={}
        if self.net:
            j['adr'] = self.netaddr
        return "%s:%s %s" % (self.name, self.domain, " ".join("%s=%s" % (k,v) for k,v in j.items()))


@InventoryRoot.register("host")
class HostRoot(NamedMixin, ClientEntry):
    def __init__(self, *a, **k):
        self._hosts = {}
        super().__init__(*a,**k)

    @classmethod
    def child_type(cls, name):
        return Host

    @property
    def _hostroot(self):
        return self

    def by_domain(self, name, create=None):
        return self.follow(*name.split('.')[::-1], create=create)

    async def update(self, value, _locked=False):
        raise ValueError("No values here!")


@InventoryRoot.register("cable")
class CableRoot(ClientEntry):
    def __init__(self, *a,**k):
        self._port2cable = {}
        super().__init__(*a,**k)

    def _running(self):
        for c in self.all_children:
            if c.dest_a is None or c.dest_b is None:
                c._resolve()

    def child_type(cls, name):
        return CableRootB

    def cable_for(self, obj, *, create=None):
        """\
            Return the cable for this node+port

            @create: if True, error if exists; if False, error if not
            otherwise cable or None
            """
        h,p = self._hp(obj)
        try:
            c = self._port2cable[h][p]
        except KeyError:
            if create is False:
                raise
            return None
        else:
            if create:
                raise ValueError("Already exists", c)
        return c

    async def link(self, obj_a, obj_b, *, wait=True):
        """\
            Link ``obj_a`` to ``obj_b``.

            Objects may be hosts or hostports.
            """

        c1 = self.cable_for(obj_a)
        c2 = self.cable_for(obj_b)
        if c1 is not None and c1 is c2:
            return
        if c1 is not None:
            await c1.delete()
        if c2 is not None:
            await c2.delete()

        client = self.root.client
        c = self.follow(client.server_name, await client.get_tock(), create=True)
        await c.link(obj_a, obj_b, wait=wait)

    async def unlink(self, dest, *, ignore=False, wait=False):
        try:
            c = self.cable_for(dest)
        except KeyError:
            if ignore:
                return
            raise KeyError(dest) from None
        await c.unlink(wait=wait)


    @staticmethod
    def _hp(dest):
        if hasattr(dest,'subpath'):
            h = dest.subpath
            p = None
        else:
            try:
                h = dest.host.subpath
            except AttributeError:
                import pdb;pdb.set_trace()
                raise
            p = dest.name
        return h,p

    async def _add_cable(self, cable):
        """
            Add this link to the cache
            """
        def aa(dest):
            h,p=self._hp(dest)
            c = self._port2cable.get(h)
            if c is None:
                self._port2cable[h] = c = WeakValueDictionary()
            oc = c.get(p)
            if oc not in (None,cable):
                logger.error("Collision: %r/%r %r/%r",cable,cable._path,oc,oc._path)
                return
            c[p] = cable
            cable.reg_del(self,'_del__cable',cable,ref(dest))

        aa(cable.dest_a)
        try:
            aa(cable.dest_b)
        except Exception:
            self._del__cable(cable,ref(cable.dest_a))
            raise


    def _del__cable(self, cable, dest):
        """
            Drop this link from the cache
            """
        dest = dest()
        if dest is None:
            return
        h,p = self._hp(dest)
        old = self._port2cable.pop(h,{}).pop(p,None)
        if old is None or old is cable:
            if not self._port2cable.get(h,True):
                del self._port2cable[h]
        else:
            self._port2cable[h][p] = old

    async def update(self, value, _locked=False):
        raise ValueError("No values here!")


class CableRootB(ClientEntry):
    """\
        Cables are unnamed and stored as (node,tock) tuple.
        """
    def child_type(cls, name):
        return Cable

    async def update(self, value, _locked=False):
        raise ValueError("No values here!")


class Cable(Cleaner, AttrClientEntry):
    ATTRS = ()

    dest_a = None
    dest_b = None
    _dest_a = None
    _dest_b = None

    @property
    def _ppar(self):
        return self.parent.parent

    def other_end(self, dest):
        if self.dest_a is dest:
            return self.dest_b
        if self.dest_b is dest:
            return self.dest_a

        if not hasattr(dest,'subpath'):
            dest = dest.host
        d_a = self.dest_a
        if not hasattr(d_a,'subpath'):
            d_a = d_a.host
        d_b = self.dest_b
        if not hasattr(d_b,'subpath'):
            d_b = d_b.host
        if d_a is dest:
            return self.dest_b
        if d_b is dest:
            return self.dest_a
        raise KeyError(dest)

    async def set_value(self, value=NotGiven):
        """\
            Called by the network to update my value.
            """
        await super().set_value(value)
        await self._resolve()

    async def _resolve(self):
        def res(dest):
            try:
                d = self.root.follow(*dest[0])
                if len(dest)>1:
                    d = d._ports[dest[1]]
            except KeyError:
                d = None
            return d

        if self.value is NotGiven:
            if self.dest_a is None:
                assert self.dest_b is None
                return
            self._ppar._del__cable(self.dest_a,ref(self.dest_b))
            self._ppar._del__cable(self.dest_b,ref(self.dest_a))
            self.dest_a = None
            self.dest_b = None
        else:
            self.dest_a = res(self.value['a'])
            self.dest_b = res(self.value['b'])
            await self._ppar._add_cable(self)
    

    async def link(self, dest_a, dest_b, *, wait=True):
        """\
            Tell this cable to record this link.
            """
        if self.value is not NotGiven:
            raise KeyError("Cable already saved")

        if len(getattr(dest_a,'port',())):
            raise ValueError("Can't link directly to a host: %r" %(dest_a,))
        if len(getattr(dest_b,'port',())):
            raise ValueError("Can't link directly to a host: %r" %(dest_b,))
        self.dest_a = dest_a
        self.dest_b = dest_b

        await self.save(wait=wait)

    async def unlink(self, *, wait=False):
        """\
            Tell this cable to not record this link.
            """
        await self.delete(wait=wait)

    async def save(self, *, wait=False):
        if self.dest_a is None or self.dest_b is None:
            await self.delete()
        else:
            await super().save(wait=wait)

    def get_value(self, **kw):
        val = super().get_value(**kw)
        def wr(dest):
            if hasattr(dest,'subpath'):
                return (dest.subpath,)
            return (dest.host.subpath,dest.name)

        val['a'] = wr(self.dest_a)
        val['b'] = wr(self.dest_b)

        return val

    async def __contains__(self, dest):
        if self.dest_a is dest:
            return True
        if self.dest_b is dest:
            return True

        if not hasattr(dest,'subpath'):
            dest = dest.host
        d_a = self.dest_a
        if not hasattr(d_a,'subpath'):
            d_a = d_a.host
        d_b = self.dest_b
        if not hasattr(d_b,'subpath'):
            d_b = d_b.host

        if d_a is dest:
            return True
        if d_b is dest:
            return True
        return False

    def __repr__(self):
        return "<Cable %r %r>" % (self.dest_a,self.dest_b)

    def __str__(self):
        if hasattr(self.dest_a,'port'):
            a = self.dest_a.name
        else:
            a = str(self.dest_a)
        if hasattr(self.dest_b,'port'):
            b = self.dest_b.name
        else:
            b = str(self.dest_b)
        return "%s %s" % (a,b)


class Wire(Cleaner, SkipNone, AttrClientEntry):
    ATTRS = ('loc','desc')
    AUX_ATTRS=('ports',)

    def __init__(self, *a,**k):
        self._ports = {'a':HostPort(self,'a',{}), 'b':HostPort(self,'b',{})}
        super().__init__(*a,**k)

    def child_type(cls, name):
        # one layer, for now.
        return ClientEntry

    @property
    def ports(self):
        r = {}
        for k,v in self._ports.items():
            vv = "-"
            c = self.root.cable.cable_for(v)
            if c is not None:
                vv = c.other_end(self)
            r[k] = vv
        return r

    @property
    def name(self):
        return self.subpath[-1]

    @property
    def port(self):
        return self._ports

    def add_port(self,name, **kw):
        if name in self._ports:
            raise KeyError("Duplicate port",name)
        p = HostPort(self,name, kw)
        self._ports[name]=p
        return p

    async def set_value(self, value=NotGiven):
        """\
            Called by the network to update my value.
            """
        await super().set_value(value)

        if value is NotGiven:
            return

        self.parent._add_name(self)

    def get_value(self):
        if self.name is not None and self.root.net.by_name(self.name) not in (self,None):
            raise KeyError("Duplicate name",self.name)

        return super().get_value()

    def __repr__(self):
        return "<Wire %s>" % (self.name,)

    def __str__(self):
        a = self.ports['a']
        if a is not None:
            a = a.cable.other_end(a)
        if a is None:
            a = '-'
        b = self.ports['b']
        if b is not None:
            b = b.cable.other_end(b)
        if b is None:
            b = '-'

        return "%s %s %s" % (self.name,a,b)


@InventoryRoot.register("wire")
class WireRoot(NamedMixin, ClientEntry):
    def __init__(self, *a, **k):
        self._hosts = {}
        super().__init__(*a,**k)

    @classmethod
    def child_type(cls, name):
        return Wire

    def by_domain(self, name, create=None):
        return self.follow(*name.split('.')[::-1], create=create)

    async def update(self, value, _locked=False):
        raise ValueError("No values here!")

