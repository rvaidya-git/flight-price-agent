import os
import sys
import json
import html
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import resend


PACIFIC = ZoneInfo("America/Los_Angeles")


def is_noon_pacific() -> bool:
    now = datetime.now(PACIFIC)
    return now.hour == 12


def get_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def fetch_flights():
    params = {
        "engine": "google_flights",
        "api_key": get_env("SERPAPI_KEY"),
        "departure_id": "SFO",
        "arrival_id": "BOM",
        "outbound_date": "2026-12-17",
        "return_date": "2027-01-02",
        "type": "1",
        "travel_class": "2",          # Premium economy
        "adults": "2",
        "children": "2",
        "currency": "USD",
        "include_airlines": "BA",     # British Airways
        "stops": "2",                 # 1 stop or fewer
        "outbound_times": "16,20",    # SFO departure: 4 PM - 8 PM
        "return_times": "0,4",        # BOM departure: midnight - 4 AM
        "sort_by": "2",              # Sort by price
        "deep_search": "true",
        "show_hidden": "true",
    }

    response = requests.get(
        "https://serpapi.com/search.json",
        params=params,
        timeout=120,
    )
    response.raise_for_status()
    return response.json()


def is_ba_result(result):
    flights = result.get("flights", [])
    if not flights:
        return False

    for flight in flights:
        airline = str(flight.get("airline", "")).lower()
        flight_number = str(flight.get("flight_number", "")).upper()

        if "british airways" not in airline and not flight_number.startswith("BA"):
            return False

    return True


def layover_count(result):
    layovers = result.get("layovers")
    if isinstance(layovers, list):
        return len(layovers)
    return None


def extract_candidates(data):
    candidates = []

    for bucket in ["best_flights", "other_flights"]:
        for result in data.get(bucket, []) or []:
            price = result.get("price")
            if not isinstance(price, int):
                continue

            candidates.append({
                "price": price,
                "bucket": bucket,
                "ba_only": is_ba_result(result),
                "layovers": layover_count(result),
                "result": result,
            })

    return sorted(candidates, key=lambda x: x["price"])


def format_summary(candidate):
    result = candidate["result"]
    lines = []

    lines.append(f"Price: ${candidate['price']}")
    lines.append(f"Result bucket: {candidate['bucket']}")
    lines.append(f"BA-only parsed result: {candidate['ba_only']}")
    lines.append(f"Parsed layovers: {candidate['layovers']}")
    lines.append("")

    for i, flight in enumerate(result.get("flights", []), start=1):
        dep = flight.get("departure_airport", {})
        arr = flight.get("arrival_airport", {})

        lines.append(f"Segment {i}")
        lines.append(f"Airline: {flight.get('airline')}")
        lines.append(f"Flight: {flight.get('flight_number')}")
        lines.append(f"From: {dep.get('id')} at {dep.get('time')}")
        lines.append(f"To: {arr.get('id')} at {arr.get('time')}")
        lines.append(f"Aircraft: {flight.get('airplane')}")
        lines.append(f"Cabin: {flight.get('travel_class')}")
        lines.append("")

    return "\n".join(lines)


def send_email(candidate, threshold):
    resend.api_key = get_env("RESEND_API_KEY")
    summary = format_summary(candidate)

    params: resend.Emails.SendParams = {
        "from": "Flight Alert <onboarding@resend.dev>",
        "to": [get_env("ALERT_TO_EMAIL")],
        "subject": f"Flight alert: BA SFO → BOM is ${candidate['price']}",
        "html": f"""
            <h2>Flight price alert</h2>
            <p><strong>SFO → BOM</strong></p>
            <p>Dates: Dec 17, 2026 to Jan 2, 2027</p>
            <p>Travelers: 2 adults, 2 children</p>
            <p>Airline: British Airways</p>
            <p>Cabin: Premium Economy</p>
            <p>Current price: <strong>${candidate['price']}</strong></p>
            <p>Threshold: <strong>${threshold}</strong></p>
            <pre>{html.escape(summary)}</pre>
        """,
    }

    resend.Emails.send(params)


def main():
    if not is_noon_pacific():
        print("Not noon Pacific. Exiting without using SerpApi.")
        return

    threshold = int(get_env("PRICE_THRESHOLD_USD"))

    data = fetch_flights()
    candidates = extract_candidates(data)

    if not candidates:
        print("No priced candidates found.")
        print(json.dumps(data, indent=2)[:4000])
        return

    cheapest = candidates[0]

    print("Cheapest candidate:")
    print(format_summary(cheapest))

    if cheapest["price"] <= threshold:
        send_email(cheapest, threshold)
        print("Alert sent.")
    else:
        print(f"No alert. Price ${cheapest['price']} > threshold ${threshold}.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise
