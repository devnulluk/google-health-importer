from app.clients import google_list_params


def test_google_list_params_only_use_supported_fields() -> None:
    assert google_list_params("heart-rate") == {"pageSize": "10000"}
    assert google_list_params("heart-rate", "next") == {
        "pageSize": "10000",
        "pageToken": "next",
    }


def test_sleep_uses_google_maximum_page_size() -> None:
    assert google_list_params("sleep") == {"pageSize": "25"}
