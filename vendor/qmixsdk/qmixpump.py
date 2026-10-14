import ctypes
from . import qmixbus
from . import qmixvalve
from enum import Enum, IntEnum
from collections import namedtuple
from .qmixbus import UnitPrefix, TimeUnit
from . import _qmixloadlib

pump_api = _qmixloadlib.load_lib("labbCAN_Pump_API")


class VolumeUnit(IntEnum):
    """
    Volume unit enumeration
    """
    litres = 68


class ForceUnit(IntEnum):
    """
    Force unit enumeration
    """
    newton = 33


class Pump(qmixbus.Device):
    """
    A pump presents the QmixSDK pump API as a python class
    """
    def __init__(self, handle = ctypes.c_longlong()):
        super().__init__(handle)

    #-------------------------------------------------------------------------
    # Initialisaton
    @staticmethod
    def get_no_of_pumps():
        """
        Query number of detected pump devices
        """
        result = pump_api.LCP_GetNoOfPumps()
        qmixbus.throw_on_error(result)
        return result


    def lookup_by_name(self, name):
        """
        Lookup for a pump device by its name.
        Initialize internal pump handle using the given name.
        """
        self.handle = ctypes.c_longlong()
        result = pump_api.LCP_LookupPumpByName(ctypes.c_char_p(name.encode('ascii')), ctypes.byref(self.handle))
        qmixbus.throw_on_error(result)


    def lookup_by_device_index(self, index):
        """
        Get pump handle by its index.
        Initialize the internal pump handle using the given index.
        """
        self.handle = ctypes.c_longlong()
        result = pump_api.LCP_GetPumpHandle(index, ctypes.byref(self.handle))
        qmixbus.throw_on_error(result)


    #-------------------------------------------------------------------------
    # Configuration
    def set_volume_unit(self, prefix : UnitPrefix, volume_unit : VolumeUnit):
        """
        Sets the default volume unit

        All parameters of subsequent dosing function calls are given in this new
        unit.
        """
        result = pump_api.LCP_SetVolumeUnit(self.handle, prefix.value, volume_unit.value)
        qmixbus.throw_on_error(result)


    def get_volume_unit(self):
        """
        Queries the current volume unit used for all dosage functions.
        Returns the volume unit as named tuple
        """
        prefix = ctypes.c_int()
        volume_unit = ctypes.c_int()
        result = pump_api.LCP_GetVolumeUnit(self.handle, ctypes.byref(prefix), ctypes.byref(volume_unit))
        qmixbus.throw_on_error(result)
        unit = namedtuple("unit", ["prefix", "unitid"])
        return unit(UnitPrefix(prefix.value), VolumeUnit(volume_unit.value))


    def set_flow_unit(self, prefix : UnitPrefix, volume_unit : VolumeUnit, time_unit : TimeUnit):
        """
        Sets the flow unit for a certain pump.

        The flow unit defines the unit to be used for all flow values passed
        to API functions or retrieved from API functions.
        """
        result = pump_api.LCP_SetFlowUnit(self.handle, prefix.value, volume_unit.value,
            time_unit.value)
        qmixbus.throw_on_error(result)


    def get_flow_unit(self):
        """
        Queries the current flow unit used for passing flow values.
        Returns the flow unit as named tuple
        """
        prefix = ctypes.c_int()
        volume_unit = ctypes.c_int()
        time_unit = ctypes.c_int()
        result = pump_api.LCP_GetFlowUnit(self.handle, ctypes.byref(prefix), 
            ctypes.byref(volume_unit), ctypes.byref(time_unit))
        qmixbus.throw_on_error(result)
        unit = namedtuple("unit", ["prefix", "unitid", "time_unitid"])
        return unit(UnitPrefix(prefix.value), VolumeUnit(volume_unit.value), TimeUnit(time_unit.value))

    
    def get_flow_rate_max(self):
        """
        Get maximum flow rate that is realizable with current dosing unit configuration.

        The maximum flow rate depends on the mechanical configuration of the
        dosing unit (gear) and on the syringe configuration. If larger syringes
        are used then larger flow rates are realizable.
        """
        maxflow = ctypes.c_double()
        result = pump_api.LCP_GetFlowRateMax(self.handle, ctypes.byref(maxflow))
        qmixbus.throw_on_error(result)
        return maxflow.value


    #-------------------------------------------------------------------------
    # Syringe Configuration
    def get_syringe_param(self):
        """
        Read syringe parameters.

        Returns the syringe parameters as named touple.
        """
        inner_diameter_mm = ctypes.c_double()
        max_piston_stroke_mm  = ctypes.c_double()
        result = pump_api.LCP_GetSyringeParam(self.handle, ctypes.byref(inner_diameter_mm),
            ctypes.byref(max_piston_stroke_mm))
        qmixbus.throw_on_error(result)
        syringe = namedtuple("syringe", ["inner_diameter_mm", "max_piston_stroke_mm"])
        return syringe(inner_diameter_mm.value, max_piston_stroke_mm.value)


    def set_syringe_param(self, inner_diameter_mm, max_piston_stroke_mm):
        """
        Set syringe parameters.

        If you change the syringe in one device, you need to setup the new
        syringe parameters to get proper conversion of flow rate und volume
        """
        result = pump_api.LCP_SetSyringeParam(self.handle, ctypes.c_double(inner_diameter_mm),
            ctypes.c_double(max_piston_stroke_mm))
        qmixbus.throw_on_error(result)


    def get_volume_max(self):
        """
        Returns the maximum volume a pump can aspirate into its container (syringe)
        """
        maxvolume = ctypes.c_double()
        result = pump_api.LCP_GetVolumeMax(self.handle, ctypes.byref(maxvolume))
        qmixbus.throw_on_error(result)
        return maxvolume.value

    #-------------------------------------------------------------------------
    # Pump control 
    def calibrate(self):
        """
        Executes a reference move for a syringe pump.
        """
        result = pump_api.LCP_SyringePumpCalibrate(self.handle)
        qmixbus.throw_on_error(result)

    
    def set_fill_level(self, level, flow):
        """
        Pumps fluid with the given flow rate until the requested fill level is reached.

        Depending on the requested fill level given in Level parameter this
        function may cause aspiration or dispension of fluid. This function only
        works properly for pump devices that support a fill level (eg. syringe
        pumps). Pumps like peristaltic pumps do not support a fill level and the
        function returns an error for unsupported pump types.
        """
        result = pump_api.LCP_SetFillLevel(self.handle, ctypes.c_double(level),
            ctypes.c_double(flow))
        qmixbus.throw_on_error(result)


    def pump_volume(self, volume, flow):
        """
        Pump a certain volume with a certain flow rate.
        """
        result = pump_api.LCP_PumpVolume(self.handle, ctypes.c_double(volume),
            ctypes.c_double(flow))
        qmixbus.throw_on_error(result)


    def dispense(self, volume, flow):
        """
        Dispense a certain volume with a certain flow rate.
        """
        result = pump_api.LCP_Dispense(self.handle, ctypes.c_double(volume),
            ctypes.c_double(flow))
        qmixbus.throw_on_error(result)


    def aspirate(self, volume, flow):
        """
        Aspirate a certain volume with a certain flow rate.
        """
        result = pump_api.LCP_Aspirate(self.handle, ctypes.c_double(volume),
            ctypes.c_double(flow))
        qmixbus.throw_on_error(result)


    def generate_flow(self, flow):
        """
        Generate a continuous flow.

        A negative flow indicates aspiration and a positiove flow indicates
        dispension.
        """
        result = pump_api.LCP_GenerateFlow(self.handle, ctypes.c_double(flow))
        qmixbus.throw_on_error(result)


    def stop_pumping(self):
        """
        Immediately stop pumping.
        """
        result = pump_api.LCP_StopPumping(self.handle)
        qmixbus.throw_on_error(result)


    @staticmethod
    def stop_all_pumps():
        """
        Immediately stop pumping off all pumps.
        """
        result = pump_api.LCP_StopAllPumps()
        qmixbus.throw_on_error(result)


    
    #-------------------------------------------------------------------------
    # Pump status 
    def get_flow_is(self):
        """
        Read the actual flow rate.
        """
        flow = ctypes.c_double()
        result = pump_api.LCP_GetFlowIs(self.handle, ctypes.byref(flow))
        qmixbus.throw_on_error(result)
        return flow.value


    def get_target_volume(self):
        """
        Read the target volume.

        This function simply returns the set target volume value
        """
        volume = ctypes.c_double()
        result = pump_api.LCP_GetTargetVolume(self.handle, ctypes.byref(volume))
        qmixbus.throw_on_error(result)
        return volume.value


    def get_dosed_volume(self):
        """
        Get the already dosed volume since last start of dosage.
        """
        volume = ctypes.c_double()
        result = pump_api.LCP_GetDosedVolume(self.handle, ctypes.byref(volume))
        qmixbus.throw_on_error(result)
        return volume.value


    def get_fill_level(self):
        """
        Returns the actual fill level of the pump.

        This function returns valid results only for pumps that support a fill level
        (eg. syringe pumps). Peristaltic pumps do not support fill level.
        For a syringe pump this function returns the current syringe fill level
        """
        level = ctypes.c_double()
        result = pump_api.LCP_GetFillLevel(self.handle, ctypes.byref(level))
        qmixbus.throw_on_error(result)
        return level.value


    def is_pumping(self):
        """
        Check if device is currently stopped or dosing.
        """
        result = pump_api.LCP_IsPumping(self.handle)
        qmixbus.throw_on_error(result)
        return True if result > 0 else False


    def is_calibration_finished(self):
        """
        Checks if calibration is finished.
        """
        result = pump_api.LCP_IsCalibrationFinished(self.handle)
        qmixbus.throw_on_error(result)
        return True if result > 0 else False 


    def is_position_sensing_initialized(self):
        """
        Returns true, if the position sensing system is properly initialized. 

        Each pump drive tracks the actual position value (volume value) by an 
        internal position counter / encoder. Some pumps, such as the new 
        Nemesys S and Nemesys M pumps, have an absolute encoder. That means, 
        this encoder always keeps its position, even if the pumps are turned of. 
        For these pumps this function always returns true. Other pumps, like 
        the Nemesys Low Pressure Pump, have an incremental encoder. This encoder
        loses its position if the pump power is turned off. That means, if such 
        a pump gets powered on, the this function returns false, because the 
        position of the incremental encoder is not initialized yet. To 
        initialize the position of the encoder, you need to do a reference move 
        via calibrate() or you need to restore a previously saved 
        encoder position via restore_position_counter_value(). 
        """  
        result = pump_api.LCP_IsPositionSensingInitialized(self.handle)
        qmixbus.throw_on_error(result)
        return True if result > 0 else False


    #-------------------------------------------------------------------------
    # Pump drive functions
    def is_enabled(self):
        """
        Query if pump drive is enabled.

        Only if the pump drive is enabled it is possible to pump fluid
        """
        result = pump_api.LCP_IsEnabled(self.handle)
        qmixbus.throw_on_error(result)
        return True if result > 0 else False

    
    def is_in_fault_state(self):
        """
        Check if pump is in a fault state.

        If the device is in fault state then it is necessary to call
        clear_fault() to clear the fault state and then enable()
        To enable the pump drive
        """
        result = pump_api.LCP_IsInFaultState(self.handle)
        qmixbus.throw_on_error(result)
        return True if result > 0 else False


    def clear_fault(self):
        """
        Clear fault condition.

        This is some kind of error acknowledge that clears the last fault and
        sets the device in an error free state. If the function
        LCP_IsInFaultState(void) indicates that device is in fault state, then
        this function may clear the fault. If the device is still in fault state
        after this function was called then a serious failure occurred
        """
        result = pump_api.LCP_ClearFault(self.handle)
        qmixbus.throw_on_error(result)


    def enable(self, enable):
        """
        Set pump drive in enabled or disabled state

        If the drive is enabled, then power is applied to the output power
        stage and the drive starts regulatig to keep its current position.
        """
        if enable:
            result = pump_api.LCP_Enable(self.handle)
        else:
            result = pump_api.LCP_Disable(self.handle) 
        qmixbus.throw_on_error(result)
        


    def get_position_counter_value(self):
        """
        Query the value of the internal drive position counter.

        You can store this value and restore it later when with the
        restore_position_counter_value() function.
        """
        counter = ctypes.c_long()
        result = pump_api.LCP_GetDrivePosCnt(self.handle, ctypes.byref(counter))
        qmixbus.throw_on_error(result)
        return counter.value


    def restore_position_counter_value(self, counter):
        """
        Restore internal hardware position counter value of pump drive.

        The function restores the internal position counter value
        saved with get_position_counter_value()
        """
        result = pump_api.LCP_RestoreDrivePosCnt(self.handle, ctypes.c_long(counter))
        qmixbus.throw_on_error(result)


    def get_pump_name(self):
        """
        Returns the device name of the pump
        """
        name = ctypes.create_string_buffer(255)
        result = pump_api.LCP_GetPumpName(self.handle, name, ctypes.sizeof(name))
        qmixbus.throw_on_error(result)
        return name.value.decode('ascii')


    #-------------------------------------------------------------------------
    # Valve functions
    def has_valve(self):
        """
        Returns true if this pump has a valve
        """
        result = pump_api.LCP_HasValve(self.handle)
        qmixbus.throw_on_error(result)
        return True if result > 0 else False


    def get_valve(self):
        """
        Returns the valve of this pump
        """
        if not hasattr(self, "valve"):
            valve_handle = ctypes.c_longlong()
            result = pump_api.LCP_GetValveHandle(self.handle,
                ctypes.byref(valve_handle))
            qmixbus.throw_on_error(result)
            self.valve = qmixvalve.Valve(valve_handle)
        return self.valve

    #-------------------------------------------------------------------------
    # Force monitoring functions
    def has_force_monitoring(self):
        """
        With this function you can check if the pump given in hPump parameter
        supports force monitoring functionality.
        """
        result = pump_api.LCP_HasForceMonitoring(self.handle)
        qmixbus.throw_on_error(result)
        return True if result > 0 else False

    
    def get_force_unit(self):
        """
        Return the force unit used for all force monitoring related functions.
        """
        prefix = ctypes.c_int()
        force_unit = ctypes.c_int()
        result = pump_api.LCP_GetForceUnit(self.handle, ctypes.byref(prefix), ctypes.byref(force_unit))
        qmixbus.throw_on_error(result)
        unit = namedtuple("unit", ["prefix", "unitid"])
        return unit(UnitPrefix(prefix.value), ForceUnit(force_unit.value))


    def enable_force_monitoring(self, enable):
        """
        Enable / Disable force monitoring.
        """
        result = pump_api.LCP_EnableForceMonitoring(self.handle, ctypes.c_int(1 if enable else 0))
        qmixbus.throw_on_error(result)


    def is_force_monitoring_enabled(self):
        """
        Returns true, if force monitoring is enabled
        """
        result = pump_api.LCP_IsForceMonitoringEnabled(self.handle)
        qmixbus.throw_on_error(result)
        return True if result > 0 else False


    def get_max_device_force(self):
        """
        Returns the maximum device force.

        The maximum device force is the maximum force the pump hardware can
        take in continuous operation.
        """
        force = ctypes.c_double()
        result = pump_api.LCP_GetMaxDeviceForce(self.handle, ctypes.byref(force))
        qmixbus.throw_on_error(result)
        return force.value


    def write_force_limit(self, force_limit):
        """
        Sets a custom force limit.

        Each device has a device specific force limit. This function allows
        you to reduce the maximum force below this maximum device force, if
        this is required for your application. If the given ForceLimit is
        higher than get_max_device_force(), then get_max_device_force() will be
        set as force limit.
        """
        result = pump_api.LCP_WriteForceLimit(self.handle, ctypes.c_double(force_limit))
        qmixbus.throw_on_error(result)


    def get_force_limit(self):
        """
        Returns the force limit.

        If no custom force limit is set, then this function returns the same
        value as get_max_device_force().
        """
        limit = ctypes.c_double()
        result = pump_api.LCP_GetForceLimit(self.handle, ctypes.byref(limit))
        qmixbus.throw_on_error(result)
        return limit.value    


    def read_force_sensor(self):
        """
        Reads the force sensor and returns the measured force in the unit
        returned by get_force_unit().
        """
        force = ctypes.c_double()
        result = pump_api.LCP_ReadForceSensor(self.handle, ctypes.byref(force))
        qmixbus.throw_on_error(result)
        return force.value


    def is_force_safety_stop_active(self):
        """
        Reads the safety stop input.

        I case of a force overload, that means, if the measured force is higher
        than the force limit, the force monitoring sets the safety stop input
        and stops the pump. If this function returns true, then the pump is in
        a force overload situation. If safety stop is active, you can read the
        force sensor via read_force_sensor() to get the current force value.
        """
        result = pump_api.LCP_IsForceSafetyStopActive(self.handle)
        qmixbus.throw_on_error(result)
        return True if result > 0 else False


    def clear_force_safety_stop(self):
        """
        Clear / acknowledge force safety stop.
        
        The force monitoring functionality has a hysteresis.
        In case of a force overload situation you need to lower the force ca.
        0.1 kN to clear the safety stop. If this is not possible, you can lower
        the force less (for example 0.02 kN) and then call this function to
        clear the safety stop input. That means, you only need to call this
        function, if it is not possible for you, to lower the force ca. 0.1 kN
        below the configured force limit or if you already lowered the force and
        the safety stop input is still active.
        """
        result = pump_api.LCP_ClearForceSafetyStop(self.handle)
        qmixbus.throw_on_error(result)
    


class ContiFlowProperty(IntEnum):
    """
    Device property identifiers to use with the set_device_property() function
    or get_device_property() function to read and write certain device 
    properties
    """
    CROSSFLOW_DURATION_S = 0
    OVERLAP_DURATION_S = 1
    MIN_PUMP_FLOW = 2
    MAX_REFILL_FLOW = 3
    REFILL_FLOW = 4
    SWITCHING_MODE = 5


class ContiFlowSwitchingMode(IntEnum):
    """
    Supported continuous flow switching modes
    """
    CROSS_FLOW = 0



class ContiFlowPump(Pump):
    """
    A conti flow pump is a virtual pump device that controls two syringe pumps
    to create an continuous flow without interruption for refill
    """

    def create(self, Pump1 : Pump, Pump2 : Pump):
        """
        Creates a pump from the two given syringe pumps

        Args:
            Pump1 (Pump): The syringe pump for the first pump channel
            Pump2 (Pump): The syringe pump for the second pump channel
        """
        result = pump_api.LCP_CreateContiFlowPump(Pump1.handle, Pump2.handle, ctypes.byref(self.handle))
        qmixbus.throw_on_error(result)


    def configure_contiflow_valve(self, PumpChannel, ValveChannel, Valve : qmixvalve.Valve, 
        AspirationPos, DispensingPos, ClosedPos):
        """
        Configures the continuous flow valve for one syringe pump channel
        of a continuous flow pump.
        
        A continuous flow pump consists of two syringe pump and each syringe
        pump channel has its own continuous flow valve.
        A continuous flow valve is a "virtual" valve that
        - may consist of a 3/2 way valve and a switchable close valve or
        - two switchable valves forming a 3/4 way valve or
        - one single 3/4 way valve

        Args:
            PumpChannel (integer): Channel index 0 or 1 for the syringe pump channel 0 or 1
            ValveChannel (integer): The index 0 or 1 of the physical valves that form a
                continuous flow valve.
            Valve (qmixvalve.Valve): The valve handle of valve that should get assigned
                to the given pump valve channel
            AspirationPos (integer): The target position index for the valve when the pump
                channel given in PumpChannel aspirates. Use -1 for no valve switching
            DispensingPos (integer): The target position index for the valve when the pump
                channel given in PumpChannelIndex dispenses. Use -1 for no valve switching
            ClosedPos (integer): The target position index for the valve when the pump
                hannel given in PumpChannelIndex needs a closed valve - e.g for pre pressurizing.
        """
        result = pump_api.LCP_ConfigureContiFlowValve(self.handle, 
            ctypes.c_uint(PumpChannel), ctypes.c_uint(ValveChannel), Valve.handle, 
            ctypes.c_int(AspirationPos), ctypes.c_int(DispensingPos), ctypes.c_int(ClosedPos))
        qmixbus.throw_on_error(result)


    def get_syringe_pump(self, PumpChannel):
        """
        Returns the syringe pump for the given pump channel index (0 or 1)
        """
        syringe_pump_handle = ctypes.c_longlong()
        result = pump_api.LCP_GetContiFlowSyringePump(self.handle, 
            ctypes.c_uint(PumpChannel), ctypes.byref(syringe_pump_handle))
        qmixbus.throw_on_error(result)
        return Pump(syringe_pump_handle)

    
    def initialize(self):
        """
        Initialize the continuous flow pump.
        Call this function after all parameters have been set, to prepare the
        conti flow pump for the start of the continuous flow. The initialization
        procedure ensures, that the syringes are sufficiently filled to start
        the continuous flow. So calling this function may cause a syringe refill
        if the syringes are not sufficiently filled. So before calling this function
        your should ensure, that syringe refilling properly works an can be
        executed. If you have a certain syringe refill procedure, you can also
        manually refill the syringes with the normal syringe pump functions. If the
        syringes are sufficiently filled if you call this function, no refilling
        will take place.  
        """
        result = pump_api.LCP_InitializeContiFlow(self.handle)
        qmixbus.throw_on_error(result)


    def is_initializing(self):
        """
        Returns true, if the conti fow pump initialization is just active.
        
        You can use this function to poll for the end for the initialization.
        """
        result = pump_api.LCP_IsContiFlowInitializing(self.handle)
        qmixbus.throw_on_error(result)
        return True if result > 0 else False

    
    def is_initialized(self):
        """
        Returns true, if the conti fow pump is initialized and ready for continuous
        flow start.

        Use this function to check if the pump is initialized before you start a
        continuous flow. If you change and continuous flow parameter, like
        valve settings, cross flow duration and so on, the pump will leave the
        initialized state. That means, after each parameter change, an initialization
        is required. Changing the flow rate or the dosing volume does not require
        and initialization.
        """
        result = pump_api.LCP_IsContiFlowInitialized(self.handle)
        qmixbus.throw_on_error(result)
        return True if result > 0 else False