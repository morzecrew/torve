"""The provider record and its loader (S-0064).

What one credential buys, as a file the engine can read: the key's variable
name, the clocks, the routes and the model roster. Nothing here resolves a
seat — that is the next phase — so these are the record's own refusals, the
shorthand-to-id rule, and the arithmetic an attempt's cost is computed by.
"""

import pytest
import yaml

from torve.config.providers import (
    Price,
    Provider,
    ProviderError,
    load_provider,
    load_providers,
    providers_dir,
)

# ----------------------- #

KEY_ENV = "SOME_PROVIDER_API_KEY"
BASE_URL = "https://api.example.com"


# ....................... #


def _write(root, name: str, body: dict) -> None:
    directory = providers_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")


# ....................... #


def _record(**overrides) -> dict:
    body: dict = {"key_env": KEY_ENV, "routes": {"openai": {"base_url": BASE_URL}}}
    body.update(overrides)

    return body


# ....................... #


def test_a_record_is_found_by_its_filename_stem(tmp_path):
    """The same rule the agent profile and the harness manifest follow: the
    name a seat resolves is the file's, unless the file says otherwise."""

    _write(tmp_path, "deepseek", _record())

    records = load_providers(tmp_path)

    assert set(records) == {"deepseek"}
    assert records["deepseek"].key_env == KEY_ENV
    assert records["deepseek"].routes["openai"].base_url == BASE_URL


# ....................... #


def test_a_records_own_name_wins_over_its_filename(tmp_path):
    _write(tmp_path, "whatever", _record(name="modelstudio"))

    assert set(load_providers(tmp_path)) == {"modelstudio"}


# ....................... #


def test_two_records_claiming_one_name_are_refused_naming_both(tmp_path):
    """The half a directory cannot enforce (S-0061/D-12): a name is what a
    seat resolves, so one of the two files has to change, and the refusal
    says which two."""

    _write(tmp_path, "first", _record(name="shared"))
    _write(tmp_path, "second", _record(name="shared"))

    with pytest.raises(ProviderError) as exc:
        load_providers(tmp_path)

    assert "first.yaml" in str(exc.value)
    assert "second.yaml" in str(exc.value)


# ....................... #


def test_no_directory_is_no_providers(tmp_path):
    """A repository that has not written any down yet, which is not an
    error."""

    assert load_providers(tmp_path) == {}


# ....................... #


def test_a_record_that_is_not_a_mapping_is_refused_by_path(tmp_path):
    directory = providers_dir(tmp_path)
    directory.mkdir(parents=True)
    (directory / "broken.yaml").write_text("- a list\n", encoding="utf-8")

    with pytest.raises(ProviderError, match="must be a mapping"):
        load_providers(tmp_path)


# ....................... #


def test_load_provider_names_what_is_present_when_the_name_is_not(tmp_path):
    _write(tmp_path, "deepseek", _record())

    with pytest.raises(ProviderError) as exc:
        load_provider(tmp_path, "anthropic")

    assert "deepseek" in str(exc.value)
    assert load_provider(tmp_path, "deepseek").key_env == KEY_ENV


# ....................... #


def test_a_provider_names_the_variable_its_credential_lives_in(tmp_path):
    """Configuration is committed and credentials are not, so the record
    carries a name (S-0064/D-1). A record with none is refused at load."""

    _write(tmp_path, "nameless", {"routes": {"openai": {"base_url": BASE_URL}}})

    with pytest.raises(ProviderError, match="key_env"):
        load_providers(tmp_path)


# ....................... #


def test_a_provider_with_no_route_is_refused_naming_the_dialects(tmp_path):
    _write(tmp_path, "routeless", {"key_env": KEY_ENV})

    with pytest.raises(ProviderError) as exc:
        load_providers(tmp_path)

    assert "openai" in str(exc.value)
    assert "anthropic" in str(exc.value)


# ....................... #


def test_a_route_keyed_by_no_known_dialect_is_refused(tmp_path):
    """`routes` is keyed by api name (S-0064/D-2), and a harness's own
    spelling is not one of them."""

    _write(tmp_path, "odd", _record(routes={"responses": {"base_url": BASE_URL}}))

    with pytest.raises(ProviderError, match="responses"):
        load_providers(tmp_path)


# ....................... #


def test_a_route_is_somewhere_to_dial(tmp_path):
    _write(tmp_path, "unreachable", _record(routes={"openai": {"base_url": "api.example.com"}}))

    with pytest.raises(ProviderError, match="http"):
        load_providers(tmp_path)


# ....................... #


def test_two_dialects_on_one_credential_are_two_routes(tmp_path):
    """A provider serving two dialects is two routes on one record, each with
    its own base URL and its own compat facts (S-0064/D-2) — the quirk that
    differs between two URLs of one provider stops being the provider's."""

    _write(
        tmp_path,
        "modelstudio",
        _record(
            routes={
                "openai": {
                    "base_url": "https://p.example/compatible-mode/v1",
                    "compat": {"thinking_format": "deepseek"},
                },
                "anthropic": {"base_url": "https://p.example/apps/anthropic"},
            }
        ),
    )

    routes = load_provider(tmp_path, "modelstudio").routes

    assert routes["openai"].compat.thinking_format == "deepseek"
    assert routes["anthropic"].compat.thinking_format == ""


# ....................... #


def test_a_value_torve_has_no_name_for_rides_as_extra(tmp_path):
    """S-0064/D-10: unvalidated and visibly so. A quirk nobody has modelled
    does not block a seat, and it stays obvious in review which values the
    engine stands behind."""

    _write(
        tmp_path,
        "odd",
        _record(routes={"openai": {"base_url": BASE_URL, "extra": {"whatever": [1, "two"]}}}),
    )

    assert load_provider(tmp_path, "odd").routes["openai"].extra == {"whatever": [1, "two"]}


# ....................... #


def test_a_compat_fact_torve_does_not_know_is_refused_rather_than_carried(tmp_path):
    """The whole point of the split: `compat` is what the engine stands
    behind, so a key it does not know belongs in `extra` or nowhere."""

    _write(
        tmp_path,
        "odd",
        _record(routes={"openai": {"base_url": BASE_URL, "compat": {"invented": True}}}),
    )

    with pytest.raises(ProviderError, match="invented"):
        load_providers(tmp_path)


# ....................... #


def test_a_roster_key_with_no_id_is_its_own_id(tmp_path):
    """S-0064/D-3: the key is what a seat writes, the `id` is what reaches the
    provider. A shorthand can be renamed without moving a regime digest; a
    key that declares no id has nothing to rename."""

    _write(
        tmp_path,
        "p",
        _record(models={"shorthand": {"id": "vendor/awkward-slug:2026-09"}, "plain": {}}),
    )

    record = load_provider(tmp_path, "p")

    assert record.id_for("shorthand") == "vendor/awkward-slug:2026-09"
    assert record.id_for("plain") == "plain"
    # Resolving a seat against the roster is the next phase's refusal, and
    # this must not start performing it early.
    assert record.id_for("unlisted") == "unlisted"


# ....................... #


def test_a_model_declares_the_reasoning_levels_it_has(tmp_path):
    """A list rather than a flag, so a seat's choice has something to be
    refused against (S-0064/D-5); empty is a model that does not reason."""

    _write(
        tmp_path,
        "p",
        _record(models={"thinker": {"reasoning": ["low", "high"]}, "plain": {}}),
    )

    models = load_provider(tmp_path, "p").models

    assert models["thinker"].reasoning == ["low", "high"]
    assert models["plain"].reasoning == []


# ....................... #


def test_an_empty_roster_is_a_configuration_not_an_error(tmp_path):
    _write(tmp_path, "deepseek", _record())

    assert load_provider(tmp_path, "deepseek").models == {}


# ....................... #


def test_an_unmeasured_model_value_is_absent_rather_than_zero(tmp_path):
    """What nobody measured is unstated: 0 for the window and the cap, and no
    price at all — which is a different fact from a price of zero."""

    _write(tmp_path, "p", _record(models={"m": {}}))

    entry = load_provider(tmp_path, "p").models["m"]

    assert entry.context_window == 0
    assert entry.max_tokens == 0
    assert entry.price is None


# ....................... #


def test_price_is_per_million_tokens(tmp_path):
    """Written the way every vendor's billing page states them; the
    arithmetic divides once (S-0064/D-12)."""

    price = Price(input=0.30, output=1.20)

    assert price.cost({"input_tokens": 1_000_000, "output_tokens": 500_000}) == 0.9


# ....................... #


def test_an_unstated_cache_rate_bills_at_the_input_rate():
    """Absent is neither a discount nor a surcharge: the engine bills what it
    knows rather than inventing a rate the vendor never published."""

    assert Price(input=1.0).cost({"cache_read_tokens": 1_000_000}) == 1.0
    assert Price(input=1.0, cache_read=0.1).cost({"cache_read_tokens": 1_000_000}) == 0.1
    assert Price(input=1.0, cache_write=2.0).cost({"cache_creation_tokens": 1_000_000}) == 2.0


# ....................... #


def test_counts_that_were_never_reported_contribute_nothing():
    """A count the adapter did not report is not a count of zero, and this is
    the only place that can still tell the two apart (S-0004/D-6)."""

    price = Price(input=1.0, output=10.0)

    assert price.cost({"input_tokens": 2_000_000}) == 2.0
    assert price.cost({}) is None
    assert price.cost({"input_tokens": None}) is None
    assert price.cost({"input_tokens": "many"}) is None


# ....................... #


def test_a_record_that_carries_everything_round_trips(tmp_path):
    """The shape a real record is written in, read back whole — the file the
    engine has never been able to read before this phase."""

    body = _record(
        name="modelstudio",
        via_proxy=True,
        request_timeout_s=600,
        stream_idle_timeout_s=120,
        models={
            "qwen3.8-flash": {
                "context_window": 1_000_000,
                "max_tokens": 65536,
                "reasoning": ["none", "low", "max"],
                "price": {"input": 0.30, "output": 1.20, "cache_read": 0.06},
            }
        },
    )
    _write(tmp_path, "modelstudio", body)

    record = load_provider(tmp_path, "modelstudio")
    entry = record.models["qwen3.8-flash"]

    assert record.via_proxy is True
    assert (record.request_timeout_s, record.stream_idle_timeout_s) == (600, 120)
    assert entry.context_window == 1_000_000
    assert entry.price is not None
    assert entry.price.cost({"input_tokens": 1_000_000, "cache_read_tokens": 1_000_000}) == 0.36


# ....................... #


def test_a_key_the_record_has_no_name_for_is_refused():
    """STRICT on the record itself: a misspelled clock is a silent default,
    and a record nobody can see the effect of is the failure this file
    exists to stop."""

    with pytest.raises(ValueError, match="upstream"):
        Provider.model_validate(
            {"key_env": KEY_ENV, "upstream": BASE_URL, "routes": {"openai": {"base_url": BASE_URL}}}
        )


# ....................... #


def test_the_repositorys_own_provider_records_load(tmp_path):
    """This repository's records are records like any other, and the loader
    that validates them is the one the runner uses."""

    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    records = load_providers(root)

    assert set(records) >= {"anthropic", "deepseek", "modelstudio"}
    # A subscription seat has no per-token cost and says so (S-0064/D-12).
    assert records["anthropic"].models["claude-opus-5"].price is None
