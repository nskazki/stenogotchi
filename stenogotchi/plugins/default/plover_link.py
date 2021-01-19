
"""
Stenogotchi and Bluetooth HID keyboard emulator D-BUS Service

Based on: https://gist.github.com/ukBaz/a47e71e7b87fbc851b27cde7d1c0fcf0#file-readme-md
Which in turn takes the original idea from: http://yetanotherpointlesstechblog.blogspot.com/2016/04/emulating-bluetooth-keyboard-with.html

Tested on:
    Python 3.7
    BlueZ 5.5
"""
import os
import sys
import logging
import dbus
import dbus.service
import socket
from time import sleep


from gi.repository import GLib
from dbus.mainloop.glib import DBusGMainLoop

if not __name__ == '__main__':
    import stenogotchi.plugins as plugins
    ObjectClass = plugins.Plugin
else:
    ObjectClass = object


class HumanInterfaceDeviceProfile(dbus.service.Object):
    """
    BlueZ D-Bus Profile for HID
    """
    fd = -1

    @dbus.service.method('org.bluez.Profile1',
                         in_signature='', out_signature='')
    def Release(self):
            logging.info('PloverLink: Release')
            mainloop.quit()

    @dbus.service.method('org.bluez.Profile1',
                         in_signature='oha{sv}', out_signature='')
    def NewConnection(self, path, fd, properties):
            self.fd = fd.take()
            logging.info('PloverLink: NewConnection({}, {})'.format(path, self.fd))
            for key in properties.keys():
                    if key == 'Version' or key == 'Features':
                            logging.info('PloverLink:  {} = 0x{:04x}'.format(key,
                                                           properties[key]))
                    else:
                            logging.info('PloverLink:  {} = {}'.format(key, properties[key]))

    @dbus.service.method('org.bluez.Profile1',
                         in_signature='o', out_signature='')
    def RequestDisconnection(self, path):
            logging.info('PloverLink: RequestDisconnection {}'.format(path))

            if self.fd > 0:
                    os.close(self.fd)
                    self.fd = -1


class BTKbDevice:
    """
    create a bluetooth device to emulate a HID keyboard
    """
    # Set default name to use for BT Keyboard
    MY_DEV_NAME = 'Bluetooth Keyboard'
    # Service port - must match port configured in SDP record
    P_CTRL = 17
    # Service port - must match port configured in SDP record#Interrrupt port
    P_INTR = 19
    # BlueZ dbus
    PROFILE_DBUS_PATH = '/bluez/yaptb/btkb_profile'
    ADAPTER_IFACE = 'org.bluez.Adapter1'
    DEVICE_INTERFACE = 'org.bluez.Device1'
    DBUS_PROP_IFACE = 'org.freedesktop.DBus.Properties'
    DBUS_OM_IFACE = 'org.freedesktop.DBus.ObjectManager'

    # file path of the sdp record to laod
    install_dir  = os.path.dirname(os.path.realpath(__file__))
    SDP_RECORD_PATH = os.path.join(install_dir,
                                   'plover_link_btserver_sdp_record.xml')
    # UUID for HID service (1124)
    # https://www.bluetooth.com/specifications/assigned-numbers/service-discovery
    UUID = '00001124-0000-1000-8000-00805f9b34fb'

    def __init__(self, hci=0):
        self._agent = plugins.loaded['plover_link']._agent
        self.scontrol = None
        self.ccontrol = None  # Socket object for control
        self.sinterrupt = None
        self.cinterrupt = None  # Socket object for interrupt
        self.dev_path = '/org/bluez/hci{}'.format(hci)
        logging.info('PloverLink: Setting up BT device')
        self.bus = dbus.SystemBus()
        self.adapter_methods = dbus.Interface(
            self.bus.get_object('org.bluez',
                                self.dev_path),
            self.ADAPTER_IFACE)
        self.adapter_property = dbus.Interface(
            self.bus.get_object('org.bluez',
                                self.dev_path),
            self.DBUS_PROP_IFACE)

        self.bus.add_signal_receiver(self.interfaces_added,
                                     dbus_interface=self.DBUS_OM_IFACE,
                                     signal_name='InterfacesAdded')

        self.bus.add_signal_receiver(self._properties_changed,
                                     dbus_interface=self.DBUS_PROP_IFACE,
                                     signal_name='PropertiesChanged',
                                     arg0=self.DEVICE_INTERFACE,
                                     path_keyword='path')

        logging.info('PloverLink: Configuring for name {}'.format(BTKbDevice.MY_DEV_NAME))

        self.config_hid_profile()

        # set the Bluetooth device configuration
        self.alias = BTKbDevice.MY_DEV_NAME
        self.discoverabletimeout = 0
        self.discoverable = True
        self.bthost_mac = None
        self.bthost_name = ""

    def interfaces_added(self):
        pass

    def _properties_changed(self, interface, changed, invalidated, path):
        if self.on_disconnect is not None:
            if 'Connected' in changed:
                if not changed['Connected']:
                    self.on_disconnect()

    def on_disconnect(self):
        logging.info('PloverLink: The client has been disconnected')
        self.bthost_mac = None
        self.bthost_name = ""
        self._agent.set_bt_disconnected()
        # Attempt to auto_connect once, then go back to listening mode
        self.auto_connect()

    @property
    def address(self):
        """Return the adapter MAC address."""
        return self.adapter_property.Get(self.ADAPTER_IFACE,
                                         'Address')

    @property
    def powered(self):
        """
        power state of the Adapter.
        """
        return self.adapter_property.Get(self.ADAPTER_IFACE, 'Powered')

    @powered.setter
    def powered(self, new_state):
        self.adapter_property.Set(self.ADAPTER_IFACE, 'Powered', new_state)

    @property
    def alias(self):
        return self.adapter_property.Get(self.ADAPTER_IFACE,
                                         'Alias')

    @alias.setter
    def alias(self, new_alias):
        self.adapter_property.Set(self.ADAPTER_IFACE,
                                  'Alias',
                                  new_alias)

    @property
    def discoverabletimeout(self):
        """Discoverable timeout of the Adapter."""
        return self.adapter_props.Get(self.ADAPTER_IFACE,
                                      'DiscoverableTimeout')

    @discoverabletimeout.setter
    def discoverabletimeout(self, new_timeout):
        self.adapter_property.Set(self.ADAPTER_IFACE,
                                  'DiscoverableTimeout',
                                  dbus.UInt32(new_timeout))

    @property
    def discoverable(self):
        """Discoverable state of the Adapter."""
        return self.adapter_props.Get(
            self.ADAPTER_INTERFACE, 'Discoverable')

    @discoverable.setter
    def discoverable(self, new_state):
        self.adapter_property.Set(self.ADAPTER_IFACE,
                                  'Discoverable',
                                  new_state)

    def config_hid_profile(self):
        """
        Setup and register HID Profile
        """

        logging.info('PloverLink: Configuring Bluez Profile')
        service_record = self.read_sdp_service_record()

        opts = {
            'Role': 'server',
            'RequireAuthentication': False,
            'RequireAuthorization': False,
            'AutoConnect': True,
            'ServiceRecord': service_record,
        }

        manager = dbus.Interface(self.bus.get_object('org.bluez',
                                                     '/org/bluez'),
                                 'org.bluez.ProfileManager1')

        HumanInterfaceDeviceProfile(self.bus,
                                    BTKbDevice.PROFILE_DBUS_PATH)

        manager.RegisterProfile(BTKbDevice.PROFILE_DBUS_PATH,
                                BTKbDevice.UUID,
                                opts)

        logging.info('PloverLink: Profile registered ')

    @staticmethod
    def read_sdp_service_record():
        """
        Read and return SDP record from a file
        :return: (string) SDP record
        """
        logging.info('PloverLink: Reading service record')
        try:
            fh = open(BTKbDevice.SDP_RECORD_PATH, 'r')
        except OSError:
            sys.exit('Could not open the sdp record. Exiting...')

        return fh.read()   

    def listen(self):
        """
        Listen for connections coming from HID client
        """

        logging.info('PloverLink: Waiting for connections')
        self.scontrol = socket.socket(socket.AF_BLUETOOTH,
                                      socket.SOCK_SEQPACKET,
                                      socket.BTPROTO_L2CAP)
        self.scontrol.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sinterrupt = socket.socket(socket.AF_BLUETOOTH,
                                        socket.SOCK_SEQPACKET,
                                        socket.BTPROTO_L2CAP)
        self.sinterrupt.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.scontrol.bind((self.address, self.P_CTRL))
        self.sinterrupt.bind((self.address, self.P_INTR))

        # Start listening on the server sockets
        self.scontrol.listen(1)  # Limit of 1 connection
        self.sinterrupt.listen(1)

        self.ccontrol, cinfo = self.scontrol.accept()
        logging.info('PloverLink: {} connected on the control socket'.format(cinfo[0]))

        self.cinterrupt, cinfo = self.sinterrupt.accept()
        logging.info('PloverLink: {} connected on the interrupt channel'.format(cinfo[0]))
        
        self.bthost_mac = cinfo[0]
        self.bthost_name = self.get_connected_device_name()

        self._agent.set_bt_connected(self.bthost_name)

    def auto_connect(self):
        bt_autoconnect_mac = plugins.loaded['plover_link']._agent._config['main']['plugins']['plover_link']['bt_autoconnect_mac']
 
        if not bt_autoconnect_mac:
            logging.info('PloverLink: No bt_autoconnect_mac set in config. Listening for incoming connections instead...')  
            self.listen()
        else:
            logging.info('PloverLink: Trying to auto connect to preferred BT host...')  
            try:
                self.ccontrol = socket.socket(socket.AF_BLUETOOTH,
                                        socket.SOCK_SEQPACKET,
                                        socket.BTPROTO_L2CAP)
                self.cinterrupt = socket.socket(socket.AF_BLUETOOTH,
                                        socket.SOCK_SEQPACKET,
                                        socket.BTPROTO_L2CAP)
                self.ccontrol.connect((bt_autoconnect_mac, self.P_CTRL))
                self.cinterrupt.connect((bt_autoconnect_mac, self.P_INTR))

                logging.info('PloverLink: Reconnected to ' + bt_autoconnect_mac)
                self.bthost_mac = bt_autoconnect_mac
                self.bthost_name = self.get_connected_device_name()
                self._agent.set_bt_connected(self.bthost_name)
    
            except Exception as ex:
                logging.info('PloverLink: Failed to auto_connect, falling back to listen mode to await new connection.' + str(ex))
                self.listen()


    def get_connected_device_name(self):
        import pydbus               # No point using both dbus and pydbus, should fix the code here to work without pydbus

        bus = pydbus.SystemBus()
        adapter = bus.get('org.bluez', self.dev_path)  # doesn't seem to do anything
        mngr = bus.get('org.bluez', '/')
        
        mngd_objs = mngr.GetManagedObjects()
        for path in mngd_objs:
            con_state = mngd_objs[path].get('org.bluez.Device1', {}).get('Connected', False)
            if con_state:
                addr = mngd_objs[path].get('org.bluez.Device1', {}).get('Address')
                name = mngd_objs[path].get('org.bluez.Device1', {}).get('Name')
                logging.info(f'PloverLink: Device {name} [{addr}] is connected')

        return name

    def send(self, msg):
        """
        Send HID message
        :param msg: (bytes) HID packet to send
        """
        self.cinterrupt.send(bytes(bytearray(msg)))


class StenogotchiService(dbus.service.Object):
    """
    Setup of a D-Bus service to receive:
        Status updates and HID messages from Plover plugin.
        HID messages from Stenogotchi evdevkb plugin
    """
    def __init__(self):
        logging.info('PloverLink: Setting up Stenogotchi D-Bus service')
        bus_name = dbus.service.BusName('com.github.stenogotchi', bus=dbus.SystemBus())
        dbus.service.Object.__init__(self, bus_name, '/com/github/stenogotchi')
        
        self._agent = plugins.loaded['plover_link']._agent
        self.device = BTKbDevice()  # create and setup our BTKbDevice
        self.device.auto_connect()     # attemt to auto connect to preferred BT host before falling back to listening for new incoming connection

    @dbus.service.method('com.github.stenogotchi', in_signature='ay')
    def send_keys(self, cmd):
        self.device.send(cmd)

    @dbus.service.method('com.github.stenogotchi', in_signature='b')
    def plover_is_running(self, b):
        logging.info('PloverLink: plover_is_running = ' + str(b))
        self._agent.set_plover_boot()

    @dbus.service.method('com.github.stenogotchi', in_signature='b')
    def plover_is_ready(self, b):
        logging.info('PloverLink: plover_is_ready = ' + str(b))
        self._agent.set_plover_ready()

    @dbus.service.method('com.github.stenogotchi', in_signature='s')
    def plover_machine_state(self, s):
        logging.info('PloverLink: plover_machine_state = ' + s)

    @dbus.service.method('com.github.stenogotchi', in_signature='b')
    def plover_output_enabled(self, b):
        logging.info('PloverLink: plover_output_enabled = ' + str(b))


class PloverLink(ObjectClass):
    __autohor__ = 'Anodynous'
    __version__ = '0.1'
    __license__ = 'MIT'
    __description__ = 'This plugin enables connectivity to Plover through D-Bus. Note that it needs root permissions due to using sockets'

    def __init__(self):
        self._agent = None       # only added to be able to do callbacks ot agent events
        self.running = False
        self._stenogotchiservice = None
        self.mainloop = None

    # called when everything is ready and the main loop is about to start
    def on_ready(self, agent):
        self._agent = agent

        DBusGMainLoop(set_as_default=True)
        self._stenogotchiservice = StenogotchiService()
        self.mainloop = GLib.MainLoop()
        
        try:
            self.mainloop.run()
            self.running = True
            logging.info("PloverLink: PloverLink is up")
        except:
            logging.error("PloverLink: Could not start PloverLink")
                    

if __name__ == '__main__':
    # The sockets require root permission
    if not os.geteuid() == 0:
        sys.exit('Only root can run this script')

    DBusGMainLoop(set_as_default=True)
    stenogotchiservice = StenogotchiService()
    mainloop = GLib.MainLoop()
    mainloop.run()
