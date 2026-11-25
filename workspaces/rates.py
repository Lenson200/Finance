import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.error import URLError
from urllib.request import urlopen

from django.utils import timezone

from workspaces.models import ExchangeRate


RATE_PROVIDER = "open.er-api.com"
RATE_MAX_AGE = timedelta(hours=24)


class ExchangeRateUnavailable(Exception):
    pass


def get_exchange_rate(from_currency, to_currency):
    from_currency = from_currency.upper()
    to_currency = to_currency.upper()
    if from_currency == to_currency:
        return Decimal("1")

    cached = ExchangeRate.objects.filter(
        from_currency_id=from_currency,
        to_currency_id=to_currency,
    ).first()
    now = timezone.now()
    if cached and cached.fetched_at >= now - RATE_MAX_AGE:
        return cached.rate

    try:
        with urlopen(
            f"https://open.er-api.com/v6/latest/{from_currency}",
            timeout=5,
        ) as response:
            data = json.loads(response.read())
        if (
            not isinstance(data, dict)
            or data.get("result") != "success"
            or data.get("base_code") != from_currency
        ):
            raise ExchangeRateUnavailable("The exchange-rate provider returned an invalid response.")
        rate = Decimal(str(data["rates"][to_currency]))
        if not rate.is_finite() or rate <= 0:
            raise ExchangeRateUnavailable("The exchange-rate provider returned an invalid rate.")
    except ExchangeRateUnavailable:
        raise
    except (
        OSError,
        TimeoutError,
        URLError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        InvalidOperation,
    ) as error:
        raise ExchangeRateUnavailable(
            "A current exchange rate is unavailable; the account was not saved."
        ) from error

    stored_rate, _ = ExchangeRate.objects.update_or_create(
        from_currency_id=from_currency,
        to_currency_id=to_currency,
        defaults={
            "rate": rate,
            "fetched_at": now,
            "provider": RATE_PROVIDER,
        },
    )
    return stored_rate.rate
