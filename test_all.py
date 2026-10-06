import urllib.request
import json

# Login
url = 'http://localhost:8000/api/v1/auth/login'
data = json.dumps({'email': 'engineer@processtwin.demo', 'password': 'demo-password-123!'}).encode('utf-8')
req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'}, method='POST')
response = urllib.request.urlopen(req)
auth = json.loads(response.read().decode())
token = auth['access_token']
org_id = auth['organizations'][0]['id']

# Test simulation
url = 'http://localhost:8000/api/v1/simulations'
equip_id = 'defac683-37ea-4ff4-b6e2-15ccb7bf1ddb'
data = json.dumps({'equipment_id': equip_id, 'temperature_c': 185, 'pressure_bar': 10, 'flow_m3_h': 72}).encode('utf-8')
req = urllib.request.Request(url, data=data, headers={'Authorization': 'Bearer ' + token, 'X-Organization-ID': org_id, 'Content-Type': 'application/json'}, method='POST')
try:
    response = urllib.request.urlopen(req)
    print('Simulation:', response.status, 'OK')
except Exception as e:
    print('Simulation error:', e)

# Test CSV import with plant_id
boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
body_parts = []
body_parts.append('--' + boundary)
body_parts.append('Content-Disposition: form-data; name="plant_id"')
body_parts.append('')
body_parts.append('88c2925c-ac05-4f7f-993d-2d39842e57d1')
body_parts.append('--' + boundary)
body_parts.append('Content-Disposition: form-data; name="dataset_name"')
body_parts.append('')
body_parts.append('Test CSV')
body_parts.append('--' + boundary)
body_parts.append('Content-Disposition: form-data; name="timestamp_column"')
body_parts.append('')
body_parts.append('timestamp')
mappings = {'mappings': [{'source_tag': 'TI_101', 'canonical_name': 'reactor.temperature', 'unit': 'degC', 'plant_tag': 'TI_101'}]}
body_parts.append('--' + boundary)
body_parts.append('Content-Disposition: form-data; name="mappings"')
body_parts.append('')
body_parts.append(json.dumps(mappings))
body_parts.append('--' + boundary)
body_parts.append('Content-Disposition: form-data; name="file"; filename="test.csv"')
body_parts.append('Content-Type: text/csv')
body_parts.append('')
body_parts.append('timestamp,TI_101')
body_parts.append('2024-01-01T00:00:00+00:00,180')
body_parts.append('--' + boundary + '--')
body_parts.append('')
body = '\r\n'.join(body_parts).encode('utf-8')
url = 'http://localhost:8000/api/v1/datasets/import'
req = urllib.request.Request(url, data=body, headers={'Authorization': 'Bearer ' + token, 'X-Organization-ID': org_id, 'Content-Type': 'multipart/form-data; boundary=' + boundary}, method='POST')
try:
    response = urllib.request.urlopen(req)
    print('CSV Import:', response.status, 'OK')
except Exception as e:
    print('CSV Import error:', e)
    if hasattr(e, 'read'):
        print('Error body:', e.read().decode())