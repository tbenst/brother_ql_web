#!/usr/bin/env python3
# simple demonstration of how to use the print image api
import requests

url = 'http://localhost:8013/api/print/image'
files = {'image': open('static/images/example_label.png', 'rb')}
requests.post(url, files=files)