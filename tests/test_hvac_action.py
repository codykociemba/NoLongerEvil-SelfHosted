from datetime import datetime

from nolongerevil.integrations.mqtt.helpers import derive_hvac_action
from nolongerevil.lib.consts import HaAction
from nolongerevil.lib.types import DeviceObject
from nolongerevil.routes.control.status import format_device_status

SERIAL = "02AA01AB501203EQ"


def test_action_reads_device_bucket():
    action = derive_hvac_action({"hvac_heater_state": True}, {"target_temperature_type": "heat"})
    assert action == HaAction.HEATING


async def test_status_reads_device_bucket(state_service, device_availability):
    await state_service.upsert_object(
        DeviceObject(
            serial=SERIAL,
            object_key=f"device.{SERIAL}",
            object_revision=1,
            object_timestamp=1000,
            value={"hvac_heater_state": True},
            updated_at=datetime.now(),
        )
    )
    status = format_device_status(SERIAL, state_service, device_availability)
    assert status["hvac"]["heater"] is True
