"""One-time LiveKit telephony setup for the Sahay voice agent.

Your SIP provider (Plivo, Twilio, Telnyx, Exotel vSIP, ...) owns the phone number; LiveKit bridges it into a room and
dispatches our worker (agent name LIVEKIT_AGENT_NAME, default sahay-voice).

  # see what exists
  uv run python ../../scripts/livekit_sip.py list

  # inbound: calls to these numbers reach the agent (also add them in Studio → Voice → Phone numbers)
  uv run python ../../scripts/livekit_sip.py inbound --numbers +918045678901

  # outbound: the trunk the agent dials through; put the printed ST_... id in LIVEKIT_SIP_TRUNK_ID
  uv run python ../../scripts/livekit_sip.py outbound --address sip.provider.example --numbers +918045678901 \
      --username USER --password PASS

Run from apps/api with LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET set (or in .env).
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api"))

from livekit import api  # noqa: E402

from app.config import settings  # noqa: E402


def client() -> api.LiveKitAPI:
    if not (settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret):
        sys.exit("Set LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET first.")
    return api.LiveKitAPI(settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret)


async def list_all() -> None:
    async with client() as lk:
        for t in (await lk.sip.list_inbound_trunk(api.ListSIPInboundTrunkRequest())).items:
            print(f"inbound  {t.sip_trunk_id}  {t.name}  numbers={list(t.numbers)}")
        for t in (await lk.sip.list_outbound_trunk(api.ListSIPOutboundTrunkRequest())).items:
            print(f"outbound {t.sip_trunk_id}  {t.name}  {t.address}  numbers={list(t.numbers)}")
        for r in (await lk.sip.list_dispatch_rule(api.ListSIPDispatchRuleRequest())).items:
            agents = [a.agent_name for a in r.room_config.agents]
            print(f"rule     {r.sip_dispatch_rule_id}  {r.name}  trunks={list(r.trunk_ids) or 'all'}  agents={agents}")


async def inbound(numbers: list[str], krisp: bool) -> None:
    async with client() as lk:
        trunk = await lk.sip.create_inbound_trunk(api.CreateSIPInboundTrunkRequest(trunk=api.SIPInboundTrunkInfo(
            name="Sahay inbound", numbers=numbers, krisp_enabled=krisp)))
        print("inbound trunk", trunk.sip_trunk_id)
        # One room per call (sahay-in-<random>), with our agent dispatched explicitly, as LiveKit recommends for SIP
        rule = await lk.sip.create_dispatch_rule(api.CreateSIPDispatchRuleRequest(dispatch_rule=api.SIPDispatchRuleInfo(
            name="Sahay voice agent", trunk_ids=[trunk.sip_trunk_id],
            rule=api.SIPDispatchRule(dispatch_rule_individual=api.SIPDispatchRuleIndividual(room_prefix="sahay-in-")),
            room_config=api.RoomConfiguration(agents=[api.RoomAgentDispatch(agent_name=settings.livekit_agent_name)]))))
        print("dispatch rule", rule.sip_dispatch_rule_id)
        print("Now point your provider's SIP trunk at your LiveKit SIP URI (LiveKit dashboard → Telephony → SIP URI).")


async def outbound(address: str, numbers: list[str], username: str, password: str, country: str) -> None:
    async with client() as lk:
        trunk = await lk.sip.create_outbound_trunk(api.CreateSIPOutboundTrunkRequest(trunk=api.SIPOutboundTrunkInfo(
            name="Sahay outbound", address=address, numbers=numbers, auth_username=username, auth_password=password,
            destination_country=country)))
        print("outbound trunk", trunk.sip_trunk_id)
        print(f"Set LIVEKIT_SIP_TRUNK_ID={trunk.sip_trunk_id} (or save it per business in Integrations → livekit).")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    i = sub.add_parser("inbound")
    i.add_argument("--numbers", nargs="+", required=True)
    i.add_argument("--krisp", action="store_true", help="LiveKit Cloud noise cancellation on the caller's audio")
    o = sub.add_parser("outbound")
    o.add_argument("--address", required=True, help="provider SIP host, e.g. sip.telnyx.com")
    o.add_argument("--numbers", nargs="+", required=True, help="caller ID number(s) you own at the provider")
    o.add_argument("--username", default="")
    o.add_argument("--password", default="")
    o.add_argument("--country", default="IN", help="destination country for region pinning")
    a = p.parse_args()
    if a.cmd == "list":
        asyncio.run(list_all())
    elif a.cmd == "inbound":
        asyncio.run(inbound(a.numbers, a.krisp))
    else:
        asyncio.run(outbound(a.address, a.numbers, a.username, a.password, a.country))


if __name__ == "__main__":
    main()
