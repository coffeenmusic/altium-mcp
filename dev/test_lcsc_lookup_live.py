"""Read-only live checks of batch lookup, exact order codes and public page fallback."""

import asyncio
import json
import logging
from pathlib import Path
import sys
import time
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))


async def run():
    import main
    import lcsc_catalog as catalog
    logging.getLogger().setLevel(logging.WARNING)
    root = REPO / ".altium-mcp" / ("lcsc_lookup_" + time.strftime("%Y%m%d_%H%M%S"))
    root.mkdir(parents=True)
    report = dict(saved=False, altium_commands=0, steps=[])

    def record(label, result):
        (root / (label + ".json")).write_text(json.dumps(result, indent=2, ensure_ascii=True), encoding="ascii")
        row = dict(label=label, **{k:result.get(k) for k in ("success", "result_count", "query_count", "error")})
        report["steps"].append(row)
        print(json.dumps(row), flush=True)
        return result

    async def lookup(label, **options):
        result = json.loads(await main.get_lcsc_component_info(None, **options))
        assert result.get("saved") is False, result
        return record(label, result)

    try:
        # The catalog tool must not dispatch even a read command to Altium.
        with patch.object(main.altium_bridge, "execute_command", side_effect=AssertionError("Catalog lookup called Altium")) as bridge:
            nxp = await lookup("nxp_exact", query="TJA1051T/3", manufacturer="NXP",
                               exact_mpn="TJA1051T/3,118", include_historical=False)
            assert nxp["success"] and nxp["result_count"] == 1, nxp
            card = nxp["components"][0]
            assert (card["lcsc_id"], card["manufacturer"], card["orderable_mpn"]) == ("C58988", "NXP", "TJA1051T/3,118")
            assert card["exact_mpn_match"] and card["manufacturer_match"]
            assert card["sales_status"] == "on_sale" and card["sales_status_evidence"]["isForeignOnsale"] is True
            wrong = await lookup("wrong_manufacturer", query="C58988", manufacturer="Tokmas")
            assert wrong["success"] and not wrong["components"] and not wrong["historical_search"]["attempted"]
            suffix = await lookup("wrong_suffix", query="C58988", exact_mpn="TJA1051T/3")
            assert suffix["success"] and not suffix["components"]
            batch = await lookup("batch", include_historical=False, queries=[
                {"id":"K07", "query":"C58988", "manufacturer":"NXP", "exact_mpn":"TJA1051T/3,118"},
                {"id":"Regulator", "query":"C136648", "qty":100},
                {"id":"Missing", "query":"C999999999999999999"},
                {"id":"K07_repeat", "query":"C58988", "manufacturer":"NXP", "exact_mpn":"TJA1051T/3,118"}])
            assert batch["partial_success"] and batch["succeeded_count"] == 3 and batch["failed_count"] == 1, batch
            assert batch["unique_query_count"] == 3
            assert [r["id"] for r in batch["results"]] == ["K07","Regulator","Missing","K07_repeat"]
            assert batch["results"][1]["components"][0]["requested_qty"] == 100
            # Force just the API-miss condition; the page fetch and parsing remain real.
            client = catalog.LcscClient()
            with patch.object(client, "get_product", side_effect=catalog.LcscNotFound("Forced API-miss fixture")):
                page = record("forced_api_miss_real_page", await asyncio.to_thread(client.lookup, "C58988", manufacturer="NXP"))
            assert page["success"] and len(page["components"]) == 1, page
            historical = page["components"][0]
            assert historical["historical"] and historical["data_source"] == "lcsc_product_page"
            assert historical["orderable_mpn"] == "TJA1051T/3,118" and historical["parameters"]
            assert historical["stock"] is None and historical["unit_price"] is None
            e04 = await lookup("e04_historical", query="C907510", manufacturer="Murata",
                               exact_mpn="DMT3N4R2U224M3DTA0", historical_url="https://item.szlcsc.com/973089.html")
            if e04["success"]:
                assert len(e04["components"]) == 1
                old = e04["components"][0]
                assert old["lcsc_id"] == "C907510" and old["orderable_mpn"] == "DMT3N4R2U224M3DTA0"
                assert old["sales_status"] == "discontinued" and old["stock"] is None
                report["e04_status"] = "verified_historical_card"
            else:
                assert not e04["complete"] and not e04["components"]
                assert any(row["status"] == "unavailable" for row in e04["historical_search"]["sources"])
                report["e04_status"] = "source_unavailable_reported_without_guessed_data"
            bridge.assert_not_called()
        report["checks_passed"] = True
    except Exception as exc:
        report.update(checks_passed=False, error=str(exc) or repr(exc))
    finally:
        (root / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="ascii")
        print("REPORT " + str(root / "report.json"), flush=True)
    assert report["checks_passed"], report.get("error")


if __name__ == "__main__":
    asyncio.run(run())
