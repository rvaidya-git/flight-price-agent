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


def format_money(value):
    if isinstance(value, int):
        return f"${value:,}"
    return "Price unavailable"


def format_duration(value):
    if value is None:
        return "N/A"

    if isinstance(value, int):
        hours = value // 60
        minutes = value % 60
        if hours and minutes:
            return f"{hours}h {minutes}m"
        if hours:
            return f"{hours}h"
        return f"{minutes}m"

    if isinstance(value, str):
        return value

    return str(value)


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
        "include_airlines": "BA",
        "stops": "2",                 # 1 stop or fewer
        "outbound_times": "16,22",
        "return_times": "0,4",
        "sort_by": "2",
        "deep_search": "true",
        "show_hidden": "true",
    }


def get_flight_results(data):
    results = []
    for bucket in ["best_flights", "other_flights"]:
        for result in data.get(bucket, []) or []:
            if isinstance(result.get("price"), int):
                results.append(result)
    return results


def get_layover_count(result):
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


def pick_best_candidate(results):
    valid = []

    for result in results:
        if not is_ba_only(result):
            continue
        if get_layover_count(result) != 1:
            continue
        valid.append(result)

    if not valid:
        return None

    return sorted(valid, key=lambda x: x.get("price", 10**9))[0]

def extract_google_flights_url(data):
    """
    Try to extract a real Google Flights deep link from SerpApi response.
    """

    # Most reliable location
    search_metadata = data.get("search_metadata", {})
    if isinstance(search_metadata, dict):
        google_url = search_metadata.get("google_flights_url")
        if isinstance(google_url, str) and google_url.startswith("http"):
            return google_url

    # Sometimes appears in search_parameters
    search_parameters = data.get("search_parameters", {})
    if isinstance(search_parameters, dict):
        google_url = search_parameters.get("google_flights_url")
        if isinstance(google_url, str) and google_url.startswith("http"):
            return google_url

    return None


def ba_booking_url():
    return "https://www.britishairways.com/travel/book/public/en_us/flightList"


def format_airport_time(airport):
    airport_id = airport.get("id", "")
    time = airport.get("time", "")
    return f"{html.escape(str(airport_id))}<br>{html.escape(str(time))}"


def format_layovers(result):
    layovers = result.get("layovers", [])
    if not layovers:
        return "<p><strong>Layover:</strong> N/A</p>"

    items = []
    for layover in layovers:
        airport = layover.get("id") or layover.get("name") or "Unknown airport"
        duration = format_duration(layover.get("duration"))
        items.append(f"{html.escape(str(airport))}: {html.escape(duration)}")

    return f"<p><strong>Layover:</strong> {'; '.join(items)}</p>"


def format_leg_html(title, result):
    flights = result.get("flights", [])

    rows = []
    for i, flight in enumerate(flights, start=1):
        dep = flight.get("departure_airport", {})
        arr = flight.get("arrival_airport", {})

        rows.append(f"""
            <tr>
                <td>{i}</td>
                <td>{html.escape(str(flight.get("flight_number", "")))}</td>
                <td>{html.escape(str(flight.get("airline", "")))}</td>
                <td>{format_airport_time(dep)}</td>
                <td>{format_airport_time(arr)}</td>
                <td>{html.escape(str(flight.get("airplane", "")))}</td>
                <td>{html.escape(str(flight.get("travel_class", "")))}</td>
                <td>{html.escape(format_duration(flight.get("duration")))}</td>
            </tr>
        """)

    total_duration = format_duration(result.get("total_duration"))

    return f"""
        <h3>{html.escape(title)}</h3>
        <p>
            <strong>Total leg duration:</strong> {html.escape(total_duration)}<br>
            <strong>Stops:</strong> {get_layover_count(result)}
        </p>

        {format_layovers(result)}

        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse: collapse;">
            <thead>
                <tr>
                    <th>Segment</th>
                    <th>Flight</th>
                    <th>Airline</th>
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


def format_leg_text(title, result):
    lines = []
    lines.append(title)
    lines.append(f"Total leg duration: {format_duration(result.get('total_duration'))}")
    lines.append(f"Stops: {get_layover_count(result)}")

    layovers = result.get("layovers", [])
    if layovers:
        for layover in layovers:
            airport = layover.get("id") or layover.get("name") or "Unknown airport"
            duration = format_duration(layover.get("duration"))
            lines.append(f"Layover: {airport} for {duration}")

    lines.append("")

    for i, flight in enumerate(result.get("flights", []), start=1):
        dep = flight.get("departure_airport", {})
        arr = flight.get("arrival_airport", {})

        lines.append(f"Segment {i}")
        lines.append(f"  Flight: {flight.get('flight_number')}")
        lines.append(f"  Airline: {flight.get('airline')}")
        lines.append(f"  From: {dep.get('id')} at {dep.get('time')}")
        lines.append(f"  To: {arr.get('id')} at {arr.get('time')}")
        lines.append(f"  Aircraft: {flight.get('airplane')}")
        lines.append(f"  Cabin: {flight.get('travel_class')}")
        lines.append(f"  Duration: {format_duration(flight.get('duration'))}")
        lines.append("")

    return "\n".join(lines)


def send_email(outbound, return_flight, total_price, threshold, outbound_data):
    resend.api_key = get_env("RESEND_API_KEY")

    google_link = extract_google_flights_url(outbound_data)

    if not google_link:
        google_link = "https://www.google.com/travel/flights"
    
    ba_link = ba_booking_url()

    outbound_html = format_leg_html("Outbound: SFO → BOM", outbound)
    return_html = format_leg_html("Return: BOM → SFO", return_flight)

    outbound_text = format_leg_text("Outbound: SFO → BOM", outbound)
    return_text = format_leg_text("Return: BOM → SFO", return_flight)

    params: resend.Emails.SendParams = {
        "from": "Flight Alert <onboarding@resend.dev>",
        "to": [get_env("ALERT_TO_EMAIL")],
        "subject": f"Flight alert: BA SFO ↔ BOM is {format_money(total_price)}",
        "html": f"""
            <div style="font-family: Arial, sans-serif; line-height: 1.45;">
                <h2>BA Premium Economy Flight Alert</h2>

                <p style="font-size: 18px;">
                    <strong>Best itinerary found:</strong> {format_money(total_price)}
                </p>

                <p>
                    <strong>Route:</strong> SFO ↔ BOM<br>
                    <strong>Dates:</strong> Dec 17, 2026 → Jan 2, 2027<br>
                    <strong>Travelers:</strong> 2 adults, 2 children<br>
                    <strong>Airline:</strong> British Airways<br>
                    <strong>Cabin:</strong> Premium Economy<br>
                    <strong>Your alert threshold:</strong> {format_money(threshold)}
                </p>

                <p>
                    <a href="{html.escape(google_link)}" style="font-size: 16px;">
                        Search this itinerary on Google Flights
                    </a>
                    <br>
                    <a href="{html.escape(ba_link)}" style="font-size: 16px;">
                        Open British Airways booking page
                    </a>
                </p>

                <p>
                    <em>
                        Note: exact booking links from Google Flights/SerpApi can expire or fail by session.
                        These links are intentionally stable search/booking entry points.
                    </em>
                </p>

                {outbound_html}
                <br>
                {return_html}
            </div>
        """,
        "text": f"""
BA Premium Economy Flight Alert

Best itinerary found: {format_money(total_price)}

Route: SFO ↔ BOM
Dates: Dec 17, 2026 → Jan 2, 2027
Travelers: 2 adults, 2 children
Airline: British Airways
Cabin: Premium Economy
Alert threshold: {format_money(threshold)}

Google Flights search:
{google_link}

British Airways booking page:
{ba_link}

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
    print(format_leg_text("Outbound: SFO → BOM", outbound))

    print("Fetching return flights...")
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
    print(format_leg_text("Return: BOM → SFO", return_flight))

    total_price = return_flight.get("price") or outbound.get("price")

    if not isinstance(total_price, int):
        print("Could not determine total itinerary price.")
        return

    print(f"Best itinerary price: {format_money(total_price)}")

    if total_price <= threshold:
        send_email(
            outbound=outbound,
            return_flight=return_flight,
            total_price=total_price,
            threshold=threshold,
            outbound_data=outbound_data,
        )
        print("Alert sent.")
    else:
        print(f"No alert. Price {format_money(total_price)} > threshold {format_money(threshold)}.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise