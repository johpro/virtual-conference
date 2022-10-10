import argparse
import sys
import time
import os
import json
import os.path as path
import bcrypt  # bcrypt
import string
import secrets
import time
import http.client
import requests
import hashlib

from urllib.parse import urlsplit
from datetime import datetime
from email.mime.image import MIMEImage

from core.auth import Authentication
#import core.schedule as schedule

alphabet = string.ascii_letters + string.digits

# def load_logo_attachment(filename):
#     with open(filename, "rb") as f:
#         attachment = MIMEImage(f.read())
#         attachment.add_header("Content-Disposition", "inline", filename=filename)
#         attachment.add_header("Content-ID", "<logo_image>")
#         return attachment

# def load_already_registered():
#     res = {}
#     if path.exists("registered.json"):
#         with open("registered.json", "r") as f:
#             res = json.load(f)
#     return res



def format_to_auth0(email, name, password_hash):
    return {
        "email": email,
        "email_verified": True,
        "name": name,
        "password_hash": password_hash.decode('utf-8'),
    }

def send_to_auth0(session, filename, access_token, connection_id):
    payload = {
        "connection_id": connection_id,
        "external_id": "import_user",
        "send_completion_email": False
    }

    files = {
        "users": open(filename, "rb")
    }

    headers = {
        'authorization': f"Bearer {access_token}"
    }

    domain = "https://" + urlsplit(session.auth0["audience"]).netloc + "/api/v2/jobs/users-imports"
    print(domain)
    response = requests.post(domain, data=payload, files=files,
                             headers=headers)
    print(response.content)

def send_create_user(auth : Authentication, access_token : str, name : str, email : str, password : str, metadata : dict) -> requests.Response:
    """create user in specified database
    """
    payload = {
        "email": email,
        "name" : name,
        "verify_email" : False,
        "password" : password,
        "user_metadata" : metadata if metadata else {},
        "connection": auth.auth0["connection"]
    }
    
    headers = {
        'Authorization': f"Bearer {access_token}"
    }

    
    domain = "https://" + auth.auth0["domain"] + "/api/v2/users"
    print(domain)
    response = requests.post(domain, json=payload, headers=headers)
    print(response.content)
    return response

def create_user(auth: Authentication, access_token : str, email : str, name : str):
    """test function to create a user on the specified auth0 database
    """
    password = generate_password(email, auth.auth0["password_secret"])
    print(f"Email: {email}")
    print(f"Password: {password}")
    
    send_create_user(auth, access_token, name, email, password, None)

def test_auth0(auth: Authentication):
    all_new = []
    email = "oc_guest@datav.is"
    name = "OC Guest"
    password_hash = generate_password_hash()
    print("Password:")
    print(password_hash.decode('utf-8'))

    all_new.append(format_to_auth0(email, name, password_hash))

    file_name = f"new_imports_{time.time_ns() / 1000}.json"
    with open(file_name, "w") as f:
        json.dump(all_new, f)
    
    print("Sending to Auth0")
    token = auth.get_auth0_token()
    send_to_auth0(auth, file_name, token, auth.auth0["connection_id"])

def get_any_password_requests():
    password_requests = []
    for f in os.listdir("./"):
        if f.startswith("password_request"):
            with open(f, "r") as fhandle:
                for l in fhandle.readlines():
                    line = l.strip()
                    if len(line) > 0:
                        password_requests.append(line)
    print(f"Got password requests {password_requests}")
    return password_requests

def get_new_eventbrite(session):
    eventbrite_event_id = session.eventbrite_event_id

    # Get the resource URI for the attendee page since we have to do the paginated
    # requests ourselves
    attendees = session.eventbrite.get_event_attendees(eventbrite_event_id)
    last_page = attendees["pagination"]["page_count"]

    # Note: Eventbrite's python SDK is half written essentially, and
    # doesn't directly support paging properly. So to load the other
    # pages we need to use the raw get call ourselves instead of 
    # being able to continue calling get_event_attendees
    # It looks like we can also directly request a page by passing page: <number>

    eventbrite_registrations = []
    # Page indices start at 1 inclusive
    for i in range(1, last_page + 1):
        print(f"Fetching eventbrite registrations page {i} of {last_page}")
        args = {
            'page': i
        }
        attendees = session.eventbrite.get(attendees.resource_uri, args)
        if not "attendees" in attendees:
            print("Error fetching eventbrite response?")
            print(attendees)
            break
        for a in attendees["attendees"]:
            eventbrite_registrations.append((
                a["profile"]["name"],
                a["profile"]["email"]
            ))

    return eventbrite_registrations

def generate_password(email : str, secret : str) -> str:
    """generates password from email using hash with secret
    """
    return hashlib.sha256((email + secret).encode('utf-8')).hexdigest()[:10]

def generate_password_hash() -> bytes:    
    """hash password with bcrypt to transmit password to auth0 without sharing the actual password
    """
    password = ''.join(secrets.choice(alphabet) for i in range(10))
    salt = bcrypt.gensalt(rounds=10)
    password_hash = bcrypt.hashpw(password, salt)
    return password_hash

def get_all(transmit_to_auth0, session, logo_attachment, max_new=-1):
    results = get_new_eventbrite(session)
    password_requests = get_any_password_requests()
    all_registered = load_already_registered()

    all_new = []
    for email, x in all_registered.items():
        if "emailed" not in x:
            x["emailed"] = False
        if not x["emailed"]:
            results.append([x["name"], x["email"]])

    now = str(datetime.utcnow())
    for x in results:
        name, email = x
        if max_new > 0 and len(all_new) >= max_new:
            break
        if len(email) == 0:
            continue
        # We use this same process to re-send someone their login info, so they could be
        # already registered
        if email not in all_registered or not all_registered[email]["emailed"]:
            print(f"adding {email}")
            # random password
            password = ""
            if email not in all_registered:
                password = ''.join(secrets.choice(alphabet) for i in range(10)).encode("utf-8")
            else:
                password = all_registered[email]["password"].encode("utf-8")

            salt = bcrypt.gensalt(rounds=10)
            password_hash = bcrypt.hashpw(password, salt)

            all_new.append(format_to_auth0(email, name, password, password_hash))
            all_registered[email] = {"name": name,
                                     "email": email,
                                     "password": password.decode('utf-8'),
                                     "date": now,
                                     "emailed": False}
        elif email in password_requests:
            print(f"Password request for {email}")
        else:
            continue
        password = all_registered[email]["password"]

        if session.email:
            time.sleep(0.1)

        try: 
            if session.email:
                send_register_email(email, session, logo_attachment, name, password)
                all_registered[email]["emailed"] = True
        except Exception as e:
            print("Error sending email {}".format(e))

    print(f"Got {len(all_new)} new registrations")

    registration_stats = {}
    registration_stats_file = "registration_stats.json"
    if os.path.isfile(registration_stats_file):
        with open("registration_stats.json", "r") as f:
            registration_stats = json.load(f)
        registration_stats["new_since_last"] += len(all_new)
    else:
        registration_stats["new_since_last"] = len(all_new)

    print(registration_stats)

    with open(registration_stats_file, "w") as f:
        json.dump(registration_stats, f)

    if len(all_new) > 0:
        file_name = f"new_imports_{time.time_ns() / 1000}.json"
        with open(file_name, "w") as f:
            json.dump(all_new, f)
        if transmit_to_auth0:
            print("Sending to Auth0")
            token = session.get_auth0_token()
            send_to_auth0(session, file_name, token, session.auth0["connection_id"])
            with open("registered.json", "w") as f:
                json.dump(all_registered, f, indent=4)
    print(f"New registrations processed at {datetime.now()}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Sync Eventbrite with auth0.')
    parser.add_argument('--test', action="store_true", help='do not start syncing, just retrieve all attendees')
    parser.add_argument('--create_user', action="store_true", help='create user for testing purposes')
    # parser.add_argument('--mail', action="store_true", help='send email for new users')
    # parser.add_argument('--auth0', action="store_true", help='send new users to auh0')
    # parser.add_argument('--limit', default=-1, type=int, help='maximum number of new users for this run')
    parser.add_argument("--name", default=None, type=str, help='Name of user to create')
    parser.add_argument("--email", default=None, type=str, help='Email of user')
    parser.add_argument("--token", default=None, type=str, help='access token')

    args = parser.parse_args()

    if args.test:
        auth = Authentication(auth0_api=True)
        test_auth0(auth)
    elif args.create_user:
        auth = Authentication(auth0_api=True)
        token = args.token if args.token else auth.get_auth0_token()
        create_user(auth, token, args.email, args.name)
    # else:
    #     session = auth.Authentication(email=args.mail, eventbrite_api=True, auth0_api=True)

    #     logo_attachment = None
    #     if args.logo:
    #         logo_attachment = load_logo_attachment(args.logo)

    #     while True:
    #         print("Checking for new registrations")
    #         get_all(args.auth0, session, logo_attachment, args.limit)
    #         time.sleep(15 * 60)


