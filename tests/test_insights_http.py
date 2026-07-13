async def test_insights_endpoint_over_http(client):
    resp = await client.post(
        "/api/v1/businesses",
        json={"shop_name": "Insights Shop", "business_type_id": 1, "currency_id": 1},
    )
    assert resp.status_code == 201, resp.text
    business = resp.json()

    resp = await client.get(f"/api/v1/businesses/{business['id']}/insights")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["business_id"] == business["id"]
    assert isinstance(body["items"], list)
    # brand-new business: just the cash-flow baseline card
    assert [i["category"] for i in body["items"]] == ["CASH_FLOW"]
