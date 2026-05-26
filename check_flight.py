import os
import sys
import json
import html
import urllib.parse
import requests
import resend


SERPAPI_ENDPOINT = "https://serpapi.com/search.json"


def get_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def serpapi_request(params):
    response = requests.get(SERPAPI_ENDPOINT, params=params, timeout=120)
    response.raise_for_status()
    return response.json()


def base_params():
    return {
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
        "outbound_times": "16,22",    # SFO departure window
        "return_times": "0,4",        # BOM departure window
        "sort_by": "2",              # Sort by price
        "deep_search": "true",
        "show_hidden": "true",
    }


def get_flight_results(data):
    results = []
    for bucket in ["best_flights", "other_flights"]:
        for result in data.get(bucket, []) or []:
            price = result.get("price")
            if isinstance(price, int):
                result["_bucket"] = bucket
                results.append(result)
    return results


def get_layover_count(result):
    layovers = result.get("layovers")
    if isinstance(layovers, list):
        return len(layovers)
    flights = result.get("flights", [])
    if flights:
        return max(0, len(flights) - 1)
    return None


def is_ba_only(result):
    flights = result.get("flights", [])
    if not flights:
        return False

    for flight in flights:
        airline = str(flight.get("airline", "")).lower()
        flight_number = str(flight.get("flight_number", "")).upper()

        if "british airways" not in airline and not flight_number.startswith("BA"):
            return False

    return True


def has_exactly_one_stop(result):
    layovers = get_layover_count(result)
    return layovers == 1


def pick_best_candidate(results):
    valid = []

    for result in results:
        if not is_ba_only(result):
            continue
        if not has_exactly_one_stop(result):
            continue
        valid.append(result)

    if not valid:
        return None

    return sorted(valid, key=lambda x: x.get("price", 10**9))[0]


def google_flights_search_url():
    query = "Google Flights SFO to BOM British Airways premium economy Dec 17 2026 Jan 2 2027"
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)


def format_segments_html(title, result):
    flights = result.get("flights", [])
    layovers = result.get("layovers", [])

    rows = []

    for i, flight in enumerate(flights, start=1):
        dep = flight.get("departure_airport", {})
        arr = flight.get("arrival_airport", {})

        rows.append(f"""
            <tr>
                <td>{i}</td>
                <td>{html.escape(str(flight.get("airline", "")))}</td>
                <td>{html.escape(str(flight.get("flight_number", "")))}</td>
                <td>{html.escape(str(dep.get("id", "")))}<br>{html.escape(str(dep.get("time", "")))}</td>
                <td>{html.escape(str(arr.get("id", "")))}<br>{html.escape(str(arr.get("time", "")))}</td>
                <td>{html.escape(str(flight.get("airplane", "")))}</td>
                <td>{html.escape(str(flight.get("travel_class", "")))}</td>
                <td>{html.escape(str(flight.get("duration", "")))}</td>
            </tr>
        """)

    layover_text = ""
    if layovers:
        layover_lines = []
        for layover in layovers:
            airport = layover.get("id") or layover.get("name") or ""
            duration = layover.get("duration", "")
            layover_lines.append(f"{airport}: {duration} minutes")
        layover_text = "<p><strong>Layovers:</strong> " + html.escape("; ".join(layover_lines)) + "</p>"

    return f"""
        <h3>{html.escape(title)}</h3>
        <p>
            <strong>Price:</strong> ${result.get("price", "N/A")}<br>
            <strong>Stops:</strong> {get_layover_count(result)}<br>
            <strong>Result bucket:</strong> {html.escape(str(result.get("_bucket", "")))}
        </p>
        {layover_text}
        <table border="1" cellpadding="6" cellspacing="0">
            <thead>
                <tr>
                    <th>Segment</th>
                    <th>Airline</th>
                    <th>Flight</th>
                    <th>Depart</th>
                    <th>Arrive</th>
                    <th>Aircraft</th>
                    <th>Cabin</th>
                    <th>Duration</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows)}
            </tbody>
        </table>
    """


def format_segments_text(title, result):
    lines = []
    lines.append(title)
    lines.append(f"Price: ${result.get('price', 'N/A')}")
    lines.append(f"Stops: {get_layover_count(result)}")
    lines.append(f"Result bucket: {result.get('_bucket', '')}")
    lines.append("")

    for i, flight in enumerate(result.get("flights", []), start=1):
        dep = flight.get("departure_airport", {})
        arr = flight.get("arrival_airport", {})

        lines.append(f"Segment {i}")
        lines.append(f"  Airline: {flight.get('airline')}")
        lines.append(f"  Flight: {flight.get('flight_number')}")
        lines.append(f"  From: {dep.get('id')} at {dep.get('time')}")
        lines.append(f"  To: {arr.get('id')} at {arr.get('time')}")
        lines.append(f"  Aircraft: {flight.get('airplane')}")
        lines.append(f"  Cabin: {flight.get('travel_class')}")
        lines.append(f"  Duration: {flight.get('duration')}")
        lines.append("")

    return "\n".join(lines)


def get_booking_options(booking_token):
    if not booking_token:
        return None

    params = base_params()
    params.pop("departure_token", None)
    params["booking_token"] = booking_token

    # SerpApi says date and advanced-filter params are ignored when booking_token is used,
    # but keeping route/traveler context is harmless.
    return serpapi_request(params)


def extract_booking_url(booking_data):
    if not booking_data:
        return None

    booking_options = booking_data.get("booking_options", []) or []

    for option in booking_options:
        for key in ["link", "booking_request", "url"]:
            value = option.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value

    return None


def send_email(outbound, return_flight, total_price, threshold, booking_url, booking_data):
    resend.api_key = get_env("RESEND_API_KEY")

    search_url = google_flights_search_url()
    final_booking_url = booking_url or search_url

    outbound_html = format_segments_html("Outbound: SFO → BOM", outbound)
    return_html = format_segments_html("Return: BOM → SFO", return_flight)

    outbound_text = format_segments_text("Outbound: SFO → BOM", outbound)
    return_text = format_segments_text("Return: BOM → SFO", return_flight)

    booking_options_html = ""
    if booking_data and booking_data.get("booking_options"):
        rows = []
        for option in booking_data.get("booking_options", [])[:5]:
            rows.append(f"""
                <li>
                    {html.escape(str(option.get("together", option.get("name", "Booking option"))))}
                    — {html.escape(str(option.get("price", "")))}
                </li>
            """)
        booking_options_html = f"""
            <h3>Booking options returned by SerpApi</h3>
            <ul>{''.join(rows)}</ul>
        """

    params: resend.Emails.SendParams = {
        "from": "Flight Alert <onboarding@resend.dev>",
        "to": [get_env("ALERT_TO_EMAIL")],
        "subject": f"Flight alert: BA SFO ↔ BOM is ${total_price}",
        "html": f"""
            <h2>Flight price alert</h2>

            <p><strong>Route:</strong> SFO ↔ BOM</p>
            <p><strong>Dates:</strong> Dec 17, 2026 to Jan 2, 2027</p>
            <p><strong>Travelers:</strong> 2 adults, 2 children</p>
            <p><strong>Airline:</strong> British Airways</p>
            <p><strong>Cabin:</strong> Premium Economy</p>
            <p><strong>Total estimated price:</strong> ${total_price}</p>
            <p><strong>Threshold:</strong> ${threshold}</p>

            <p>
                <a href="{html.escape(final_booking_url)}">
                    Open booking/search page
                </a>
            </p>

            {outbound_html}
            {return_html}
            {booking_options_html}

            <hr>
            <p>
                Note: Google Flights booking links can expire or resolve differently by session.
                If the direct booking link does not work, use the Google Flights search link.
            </p>
        """,
        "text": f"""
Flight price alert

Route: SFO ↔ BOM
Dates: Dec 17, 2026 to Jan 2, 2027
Travelers: 2 adults, 2 children
Airline: British Airways
Cabin: Premium Economy
Total estimated price: ${total_price}
Threshold: ${threshold}

Booking/search link:
{final_booking_url}

{outbound_text}

{return_text}
        """,
    }

    resend.Emails.send(params)


def main():
    threshold = int(get_env("PRICE_THRESHOLD_USD"))

    print("Fetching outbound flights...")
    outbound_data = serpapi_request(base_params())
    outbound_results = get_flight_results(outbound_data)
    outbound = pick_best_candidate(outbound_results)

    if not outbound:
        print("No valid BA one-stop outbound flight found.")
        print(json.dumps(outbound_data, indent=2)[:4000])
        return

    departure_token = outbound.get("departure_token")
    if not departure_token:
        print("Outbound flight found, but no departure_token was returned.")
        print(json.dumps(outbound, indent=2)[:4000])
        return

    print("Selected outbound:")
    print(format_segments_text("Outbound: SFO → BOM", outbound))

    print("Fetching return flights using departure_token...")
    return_params = base_params()
    return_params["departure_token"] = departure_token
    return_data = serpapi_request(return_params)
    return_results = get_flight_results(return_data)
    return_flight = pick_best_candidate(return_results)

    if not return_flight:
        print("No valid BA one-stop return flight found.")
        print(json.dumps(return_data, indent=2)[:4000])
        return

    print("Selected return:")
    print(format_segments_text("Return: BOM → SFO", return_flight))

    # In the return-token response, the return candidate price is usually the full round-trip price.
    # If not present, fall back to outbound + return, but that may overstate the fare.
    total_price = return_flight.get("price") or outbound.get("price")
    if not isinstance(total_price, int):
        print("Could not determine total price.")
        return

    print(f"Estimated round-trip price: ${total_price}")

    booking_token = return_flight.get("booking_token")
    booking_data = None
    booking_url = None

    if booking_token:
        print("Fetching booking options using booking_token...")
        booking_data = get_booking_options(booking_token)
        booking_url = extract_booking_url(booking_data)
    else:
        print("No booking_token returned. Email will include Google Flights search link only.")

    if total_price <= threshold:
        send_email(
            outbound=outbound,
            return_flight=return_flight,
            total_price=total_price,
            threshold=threshold,
            booking_url=booking_url,
            booking_data=booking_data,
        )
        print("Alert sent.")
    else:
        print(f"No alert. Price ${total_price} > threshold ${threshold}.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise
