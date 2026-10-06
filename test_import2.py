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

# Test CSV import with multipart - include plant_id
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

mappings = {
    'mappings': [
        {'source_tag': 'TI_101', 'canonical_name': 'reactor.temperature', 'unit': 'degC', 'plant_tag': 'TI_101'},
        {'source_tag': 'PI_101', 'canonical_name': 'reactor.pressure', 'unit': 'bar', 'plant_tag': 'PI_101'},
        {'source_tag': 'FI_101', 'canonical_name': 'reactor.feed_flow', 'unit': 'kg/h', 'plant_tag': 'FI_101'},
        {'source_tag': 'AI_101', 'canonical_name': 'reactor.feed_concentration', 'unit': 'mol/L', 'plant_tag': 'AI_101'},
    ]
}
body_parts.append('--' + boundary)
body_parts.append('Content-Disposition: form-data; name="mappings"')
body_parts.append('')
body_parts.append(json.dumps(mappings))

body_parts.append('--' + boundary)
body_parts.append('Content-Disposition: form-data; name="file"; filename="test.csv"')
body_parts.append('Content-Type: text/csv')
body_parts.append('')
body_parts.append('timestamp,TI_101,PI_101,FI_101,AI_101')
body_parts.append('2024-01-01T00:00:00+00:00,180,10.1,72,1.5')
body_parts.append('2024-01-01T00:01:00+00:00,181,10.2,73,1.6')
body_parts.append('2024-01-01T00:02:00+00:00,179,10.0,71,1.4')

body_parts.append('--' + boundary + '--')
body_parts.append('')

body = '\r\n'.join(body_parts).encode('utf-8')

url = 'http://localhost:8000/api/v1/datasets/import'
req = urllib.request.Request(url, data=body, headers={
    'Authorization': 'Bearer ' + token,
    'X-Organization-ID': org_id,
    'Content-Type': 'multipart/form-data; boundary=' + boundary
}, method='POST')
try:
    response = urllib.request.urlopen(req)
    print('Import:', response.status, json.loads(response.read().decode()))
except Exception as e:
    print('Import error:', e)
    if hasattr(e, 'read'):
        print('Error body:', e.read().decode())