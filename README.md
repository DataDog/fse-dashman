# dashman

## Dashboard helper utility
    - Copy dashboards between orgs
    - Combine multiple dashboards
    - Edit dashboard settings
    - Convert screenboards to timeboards and vice versa.

Instructions:

1. Clone this repo
2. Optionally add default API/APP keys and Dashboard Id in .env
3. `docker-compose up`
4. Point your browser to http://127.0.0.1:5050

Alternatively, you can run in a python venv and pip install requirements.txt.

### Known issues
1. Integration preset (default) dashboards cannot be used directly.  The dashboard will need to be cloned and the new id provided.
