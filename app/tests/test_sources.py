async def test_list_source_types(client):
  response = await client.get("/source-types")
  assert response.status_code == 200
  types = {source_type["backend"]: source_type for source_type in response.json()}
  assert set(types) == {"s3", "switchdrive"}
  required = {backend: [option["name"] for option in source_type["options"] if option["required"]] for backend, source_type in types.items()}
  assert required == {"s3": ["bucket"], "switchdrive": ["share_url"]}
