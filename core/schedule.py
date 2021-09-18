import re
import os
import sys
import time
import pprint
import json
import requests
import string
import secrets
import base64
import discord
import ics

from email import encoders
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from datetime import timezone, datetime, timedelta
from googleapiclient.http import MediaIoBaseUpload

import core.excel_db as excel_db
import core.auth as conf_auth
import core.thumbnail as thumbnail

# Your conference time zone
# VIS2021 is in Lousiana end of Oct: Central Daylight Time
# UTC-5
conf_tz = timezone(-timedelta(hours=5))

# Your conference name
CONFERENCE_NAME = "VIS Testing 2021"
# NOTE: This should be a URL to a wide aspect ratio conference logo image
# See the image at the URL for an example
CONFERENCE_LOGO_URL = "https://i.imgur.com/amRNJoR.png"
# NOTE: This should be a URL to your a square conference icon image
# See the image at the URL for an example
CONFERENCE_ICON_URL = "https://i.imgur.com/amRNJoR.png"
CONFERENCE_YEAR = 2021

match_timeslot = re.compile("(\d\d)(\d\d)-(\d\d)(\d\d)")

# Parse the HHMM-HHMM start-end info for a time slot into a datetime
def parse_time_slot(time_slot, month, day):
    m = match_timeslot.match(time_slot)
    start = datetime(CONFERENCE_YEAR, month, day, hour=int(m.group(1)), minute=int(m.group(2)), tzinfo=conf_tz)   
    end = datetime(CONFERENCE_YEAR, month, day, hour=int(m.group(3)), minute=int(m.group(4)), tzinfo=conf_tz)
    return (start, end)

def format_time_slot(start, end):
    return start.strftime("%H%M") + "-" + end.strftime("%H%M")

def format_time(time):
    return time.strftime("%a %b %d %H:%M %Z")

def format_time_iso8601_utc(time):
    return time.astimezone(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def generate_password():
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for i in range(8))

def match_youtube_id(url):
    m = re.match("https:\/\/youtu\.be\/(.+)", url)
    return m.group(1)

def make_youtube_title(title):
    # Make sure title is valid for Youtube: <= 100 characters and no '<' or '>' symbols
    title = title.replace("<", " ").replace(">", " ")
    if len(title) > 100:
        title = title[0:99]
    return title

def make_youtube_description(description):
    # Similar rules for the description as the title, but max length of 5000 characters
    description = description.replace("<", " ").replace(">", " ")
    if len(description) > 5000:
        description = description[0:4999]
    return description

# Get the (guild id, channel id) from the URL
def match_discord_url(url):
    m = re.match("https:\/\/discord.com\/channels\/(\d+)\/(\d+)", url)
    return (m.group(1), m.group(2))

def match_discord_channel_id(url):
    return match_discord_url(url)[1]

def match_discord_guild_id(url):
    return match_discord_url(url)[0]

def base_discord_embed():
    return {
        "thumbnail": {
            "url": CONFERENCE_LOGO_URL
        },
        "author": {
            "name": CONFERENCE_NAME,
            "icon_url": CONFERENCE_ICON_URL
        },
        "color": discord.Colour.from_rgb(218, 71, 38).value,
        "type": "rich",
        "description": "",
        "fields": []
    }

# recipients and cc_recipients should be lists of emails
# attachments should be a list of MIMEBase objects, one for each attachment
def send_html_email(subject, body, recipients, email, cc_recipients=None, attachments=None, alternative_text=None):
    message = MIMEMultipart("mixed")
    message.set_charset("utf8")

    if type(recipients) == str:
        recipients = [recipients]

    if not "SUPERMINISTREAM_EMAIL_FROM" in os.environ:
        print("You must set $SUPERMINISTREAM_EMAIL_FROM to the email address to populate the from field")
        sys.exit(1)

    all_recipients = [r.strip() for r in recipients]
    message["to"] = ", ".join(recipients)
    message["from"] = os.environ["SUPERMINISTREAM_EMAIL_FROM"]

    if cc_recipients:
        if type(cc_recipients) == str:
            cc_recipients = [cc_recipients]
        message["cc"] = ", ".join(cc_recipients)
        all_recipients += [r.strip() for r in cc_recipients]

    message["subject"] = subject
    message_text = MIMEMultipart("alternative")
    if alternative_text:
        message_text.attach(MIMEText(alternative_text, "plain", "utf8"))
    message_text.attach(MIMEText(body, "html", "utf8"))
    message.attach(message_text)

    if attachments:
        for a in attachments:
            encoders.encode_base64(a)
            message.attach(a)

    response = email.send_raw_email(
        Source=message["from"],
        Destinations=all_recipients,
        RawMessage={
            "Data": message.as_bytes()
        })
    print(response)

class Database:
    def __init__(self, workbook_name, youtube=False, email=False, use_pickled_credentials=False):
        self.workbook = excel_db.open(workbook_name)
        if youtube or email:
            self.auth = conf_auth.Authentication(youtube=youtube, email=email, use_pickled_credentials=use_pickled_credentials)
        else:
            self.auth = None

        self.computers = self.workbook.get_table("computers")

    def get_day(self, day):
        return Day(self, self.workbook.get_table(day))

    def save(self, output):
        self.workbook.save(output)

    # Lookup the youtube stream key IDs for each computer and fill in
    # the sheet. Note that these are not the same as the stream keys
    def populate_stream_key_ids(self):
        # Note: if more than 50 stream keys in the account, have to request multiple
        # pages of results using the page tokens
        live_streams = self.auth.youtube.liveStreams().list(
            part="id,snippet,cdn,status",
            mine=True,
            maxResults=50).execute()
        for c in self.computers.items():
            for s in live_streams["items"]:
                if c["Youtube Stream Key"].value == s["cdn"]["ingestionInfo"]["streamName"]:
                    c["Youtube Stream Key ID"].value = s["id"]

    def get_computer(self, computer_id):
        return [c for c in self.computers.items() if c["ID"].value == computer_id][0]

class Day:
    def __init__(self, database, sheet):
        self.database = database
        self.sheet = sheet

        # Get the month and day from the sheet
        match_day = re.compile("\w+ (\d+)/(\d+)")
        m = match_day.match(self.sheet.entry(2, 1).value)
        self.month = int(m.group(1))
        self.day = int(m.group(2))

    def entry(self, row, item):
        return self.sheet.entry(row, item)

    def get_sessions(self, include_breaks):
        # Session information starts on row 3
        sessions = {}
        for r in range(3, self.sheet.table.max_row + 1):
            event_name = self.entry(r, "Event").value
            session_name = self.entry(r, "Session").value
            if not include_breaks and event_name == "BREAK":
                continue
            if event_name == None:
                continue
            session_key = event_name + "-" + session_name
            if not session_key in sessions:
                sessions[session_key] = Session(event_name, session_name, self)
            sessions[session_key].timeslots.append(r)
        return sessions

class Session:
    def __init__(self, event, name, day):
        self.event = event
        self.name = name
        self.day = day
        self.auth = day.database.auth
        self.timeslots = []

    def num_timeslots(self):
        return len(self.timeslots)

    def timeslot_entry(self, t, item):
        return self.day.entry(self.timeslots[t], item)

    # Get the (start, end) time of a specific time slot
    def timeslot_time(self, t):
        return parse_time_slot(self.timeslot_entry(t, "Time Slot").value, self.day.month, self.day.day)

    def get_track(self):
        return self.timeslot_entry(0, "Computer").value

    # Get the (start, end) time of the entire session
    # The start time is the start of the first time slot in the session,
    # the end time is the end time of the last time slot
    def session_time(self):
        return (parse_time_slot(self.timeslot_entry(0, "Time Slot").value, self.day.month, self.day.day)[0],
                parse_time_slot(self.timeslot_entry(len(self.timeslots) - 1, "Time Slot").value, self.day.month, self.day.day)[1])

    def event_session_title(self):
        if self.event == self.name:
            return self.event
        return "{} - {}".format(self.event, self.name)

    def youtube_broadcast_id(self):
        return match_youtube_id(self.timeslot_entry(0, "Youtube Broadcast").value)

    def discord_channel_id(self):
        return match_discord_channel_id(self.timeslot_entry(0, "Discord Link").value)

    def discord_guild_id(self):
        return match_discord_guild_id(self.timeslot_entry(0, "Discord Link").value)

    def has_discord_channel(self):
        return self.timeslot_entry(0, "Discord Link").value != None

    def discord_ids(self):
        return match_discord_url(self.timeslot_entry(0, "Discord Link").value)

    def setup_time(self):
        if self.timeslot_entry(0, "Time Slot Type").value != "Zoom Only":
            return timedelta(minutes=15)
        return timedelta(minutes=0)

    def special_notes(self):
        notes = set()
        for t in range(0, len(self.timeslots)):
            if self.timeslot_entry(t, "Special Notes").value:
                for n in self.timeslot_entry(t, "Special Notes").value.split("|"):
                    notes.add(n)
            if self.timeslot_entry(t, "Speaker Photo").value:
                notes.add("Has keynote speaker photo")
            if self.timeslot_entry(t, "Custom Title Image").value:
                notes.add("Uses custom title image")
        return notes

    def get_stream_key(self):
        computer = self.timeslot_entry(0, "Computer").value
        return self.day.database.get_computer(computer)["Youtube Stream Key"].value

    def get_stream_status(self):
        computer = self.timeslot_entry(0, "Computer").value
        stream_key_id = self.day.database.get_computer(computer)["Youtube Stream Key ID"].value
        response = self.auth.youtube.liveStreams().list(
            id=stream_key_id,
            part="status"
        ).execute()
        return response["items"][0]["status"]["streamStatus"], response["items"][0]["status"]["healthStatus"]["status"]

    def get_broadcast_status(self):
        response = self.auth.youtube.liveBroadcasts().list(
            id=self.youtube_broadcast_id(),
            part="status"
        ).execute()
        return response["items"][0]["status"]["lifeCycleStatus"]

    def get_broadcast_statistics(self):
        response = self.auth.youtube.videos().list(
            id=self.youtube_broadcast_id(),
            part="liveStreamingDetails"
        ).execute()
        return response["items"][0]["liveStreamingDetails"]

    def start_streaming(self):
        timeslot_type = self.timeslot_entry(0, "Time Slot Type").value
        if timeslot_type == "Zoom Only" or timeslot_type == "Discord Only":
            print("Not streaming Zoom/Discord only event")
            return
        computer = self.timeslot_entry(0, "Computer").value
        if "Manual Stream" in self.special_notes():
            print("Stream for {} must be assigned to computer {} and advanced manually".format(
                self.event_session_title(), computer))
            return

        computer_info = self.day.database.get_computer(computer)
        stream_key = computer_info["Youtube Stream Key"].value
        stream_key_id = computer_info["Youtube Stream Key ID"].value

        broadcast_status = self.get_broadcast_status()
        # Broadcast could be in the ready state (configured and a stream key was bound),
        # or in the created state (configured but no stream key attached yet).
        if broadcast_status != "ready" and broadcast_status != "created":
            print("Broadcast {} is in state {}, and cannot be (re-)made live".format(self.youtube_broadcast_id(), broadcast_status))
            return

        # Attach the stream to the broadcast
        print("Attaching stream '{}' to '{}'".format(stream_key, self.youtube_broadcast_id()))
        self.auth.youtube.liveBroadcasts().bind(
            id=self.youtube_broadcast_id(),
            part="status",
            streamId=stream_key_id,
        ).execute()

        # Start the Zoom meeting livestream
        zoom_params = {
            "action": "start"
        }
        requests.patch("https://api.zoom.us/v2/meetings/{}/livestream/status".format(self.get_zoom_meeting_id()),
            json=zoom_params, headers=self.auth.zoom)

        # Wait about 10s for the Zoom stream to connect
        print("Sleeping 10s for Zoom live stream to begin")
        time.sleep(10)

        # Check the status of the live stream to make sure it's running before we make it live
        retries = 0
        stream_status, stream_health = self.get_stream_status()
        if stream_status != "active":
            print(f"Stream on computer {computer} (key {stream_key}) for" +
                f"broadcast {self.youtube_broadcast_id()} is not active (currently {stream_status})." +
                "will wait 10s longer for Zoom and retry")
            time.sleep(10)
            retries = retries + 1
            if retries >= 2:
                print(f"Retried {retries} times and zoom stream is still not live!?")

        if stream_health != "good":
            print("WARNING: Stream on computer {} (key {}) is active, but not healthy. Health status is {}".format(
                computer, stream_key, stream_health))

        # Make the broadcast live
        self.auth.youtube.liveBroadcasts().transition(
            broadcastStatus="live",
            id=self.youtube_broadcast_id(),
            part="status"
        ).execute()

    def stop_streaming(self):
        timeslot_type = self.timeslot_entry(0, "Time Slot Type").value
        if timeslot_type == "Zoom Only" or timeslot_type == "Discord Only":
            print("No stream to stop for Zoom/Discord only event")
            return

        computer = self.timeslot_entry(0, "Computer").value
        if "Manual Stream" in self.special_notes():
            print("Stream for {} must be stopped manually".format(
                self.event_session_title(), computer))
            return

        broadcast_status = self.get_broadcast_status()
        if broadcast_status == "complete":
            print("Broadcast {} has already been made complete, skipping redundant transition".format(self.youtube_broadcast_id()))
            return

        if broadcast_status != "live":
            print("Broadcast {} is {}, not live, cannot make complete".format(self.youtube_broadcast_id(), broadcast_status))
            return

        # Transition broadcast to complete
        self.auth.youtube.liveBroadcasts().transition(
            broadcastStatus="complete",
            id=self.youtube_broadcast_id(),
            part="status"
        ).execute()

        # Detach the stream from this broadcast so it can be reused
        self.auth.youtube.liveBroadcasts().bind(
            id=self.youtube_broadcast_id(),
            part="status"
        ).execute()

        # Make sure the video archive is also embeddable
        self.auth.youtube.videos().update(
            part="id,contentDetails,status",
            body={
                "id": self.youtube_broadcast_id(),
                "status": {
                    "embeddable": True,
                }
            }
        ).execute()

        # Stop the Zoom meeting livestream
        zoom_params = {
            "action": "stop"
        }
        requests.patch("https://api.zoom.us/v2/meetings/{}/livestream/status".format(self.get_zoom_meeting_id()),
            json=zoom_params, headers=self.auth.zoom)

    # Create the virtual aspects of the session to be streamed by the specified computer
    def create_virtual_session(self, computer, thumbnail_params):
        for t in range(0, len(self.timeslots)):
            self.timeslot_entry(t, "Computer").value = computer
        if self.timeslot_entry(0, "Time Slot Type").value != "Zoom Only":
            self.schedule_youtube_broadcast(thumbnail_params)
        self.schedule_zoom()

    # Schedule the Zoom meeting for the session and populate the sheet
    def schedule_zoom(self):
        computer = self.timeslot_entry(0, "Computer").value

        # First get our user info (we'll just have 1 per-account I guess?)
        headers = self.auth.zoom
        r = requests.get("https://api.zoom.us/v2/users?status=active&page_size=30&page_number=1", headers=headers).json()
        # Find the computer assigned to host the meeting and make them the main host, all
        # others are alternative hosts. NOTE: Each computer's Zoom account is identified by
        # having its last name be its computer ID letter
        host = None
        alternative_hosts = []
        for user in r["users"]:
            if user["last_name"] == computer:
                host = user["id"]
            else:
                alternative_hosts.append(user["id"])
        # For testing: if user doesn't exist just take the first one (i.e., on my account)
        if not host:
            print("WARNING: TESTING CODE in schedule_zoom: Picking first host after not finding track ID Zoom host")
            host = r["users"][0]["id"]
            alternative_hosts = []

        session_time = self.session_time()
        # Zoom meetings start 15min ahead of time to set up, and can run 10min over
        zoom_start = session_time[0] - self.setup_time()
        zoom_end = session_time[1] + timedelta(minutes=10)
        meeting_topic = CONFERENCE_NAME + ": " + self.event_session_title()
        # Max Zoom meeting topic length is 200 characters
        if len(meeting_topic) > 200:
            meeting_topic = meeting_topic[0:199]
        # Max agenda length is 2000 characters
        meeting_agenda = str(self)
        if len(meeting_agenda) > 2000:
            meeting_agenda = meeting_agenda[0:1999]

        meeting_info = {
            "topic": meeting_topic,
            "type": 2,
            "start_time": zoom_start.astimezone(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "timezone": "UTC",
            "duration": int((zoom_end - zoom_start).total_seconds() / 60.0),
            "password": generate_password(),
            "agenda": meeting_agenda,
            "settings": {
                "host_video": False,
                "participant_video": False,
                "join_before_host": False,
                "mute_upon_entry": True,
                "waiting_room": True,
                "audio": "both",
                "alternative_hosts": ",".join(alternative_hosts),
                "global_dial_in_countries": [
                    # NOTE: Fill in dial in countries as appropriate for your conference
                    "DE",
                    "SE",
                    "JP",
                    "KR",
                    "GB",
                    "US",
                    "CA"
                ]
            }
        }

        zoom_info = requests.post("https://api.zoom.us/v2/users/{}/meetings".format(host),
                json=meeting_info, headers=headers).json()

        # Set up the zoom meeting to live stream to our Youtube broadcast
        livestream_info = {
            "stream_url": "rtmp://a.rtmp.youtube.com/live2",
            "stream_key": self.get_stream_key(),
            "page_url": self.timeslot_entry(0, "Youtube Broadcast").value
        }
        add_livestream = requests.patch("https://api.zoom.us/v2/meetings/{}/livestream".format(zoom_info["id"]),
                json=livestream_info, headers=headers)
        if add_livestream.status_code != 204:
            print(f"ERROR: Failed to set live stream for Zoom meeting {meeting_topic}")
            sys.exit(1)

        # Fill in the Zoom info in the sheet
        for t in range(0, len(self.timeslots)):
            self.timeslot_entry(t, "Zoom URL").value = zoom_info["join_url"]
            self.timeslot_entry(t, "Zoom Meeting ID").value = str(zoom_info["id"])
            self.timeslot_entry(t, "Zoom Password").value = meeting_info["password"]

    def get_zoom_meeting_id(self):
        return self.timeslot_entry(0, "Zoom Meeting ID").value

    def get_zoom_meeting_info(self):
        # We don't keep this huge list of numbers in the spreadsheet, so we need to fetch it when needed
        headers = self.auth.zoom
        meeting_id = self.get_zoom_meeting_id()
        meeting_info = requests.get("https://api.zoom.us/v2/meetings/{}".format(meeting_id), headers=headers).json()
        return meeting_info

    def make_youtube_title(self):
        # Make sure title is valid for Youtube: <= 100 characters and no '<' or '>' symbols
        return make_youtube_title(CONFERENCE_NAME + ": {}".format(self.event_session_title()))

    def make_youtube_description(self):
        # Similar rules for the description as the title, but max length of 5000 characters
        return make_youtube_description(str(self))

    # Schedule the Youtube broadcast for the sessions and populate the sheet
    def schedule_youtube_broadcast(self, thumbnail_params):
        title = self.make_youtube_title()
        description = self.make_youtube_description()
        session_time = self.session_time()
        enable_captions = "Live Captions" in self.special_notes()
        broadcast_info = self.auth.youtube.liveBroadcasts().insert(
            part="id,snippet,contentDetails,status",
            body={
                "contentDetails": {
                    "closedCaptionsType": "closedCaptionsHttpPost" if enable_captions else "closedCaptionsDisabled",
                    "enableContentEncryption": False,
                    "enableDvr": True,
                    # Note: YouTube requires you to have 1k subscribers and 4k public watch hours
                    # to enable embedding live streams. You can set this to true if your account
                    # meets this requirement and you've enabled embedding live streams
                    "enableEmbed": True,
                    "enableAutoStart": False,
                    "enableAutoEnd": False,
                    "recordFromStart": True,
                    "startWithSlate": False,
                    # We must use a low latency only stream if using live captions
                    "latencyPreference": "low" if enable_captions else "ultraLow",
                    "monitorStream": {
                        "enableMonitorStream": False,
                        "broadcastStreamDelayMs": 0
                    }
                },
                "snippet": {
                    "title": title,
                    "scheduledStartTime": session_time[0].astimezone(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0Z"),
                    "description": description,
                },
                "status": {
                    "privacyStatus": "unlisted"
                }
            }
        ).execute()

        # Due to a bug in the Youtube Broadcast API we have to set the made for
        # kids flag through the videos API separately
        update_resp = self.auth.youtube.videos().update(
            part="id,contentDetails,status",
            body={
                "id": broadcast_info["id"],
                "status": {
                    "selfDeclaredMadeForKids": False,
                }
            }
        ).execute()

        # Render the thumbnail for the session and upload it
        if thumbnail_params:
            thumbnail_img = self.render_thumbnail(thumbnail_params)

            self.auth.youtube.thumbnails().set(
                videoId=broadcast_info["id"],
                media_body=MediaIoBaseUpload(thumbnail_img, mimetype="image/png")
            ).execute()

        for t in range(0, len(self.timeslots)):
            self.timeslot_entry(t, "Youtube Control Room").value = \
                    "https://studio.youtube.com/video/{}/livestreaming".format(broadcast_info["id"])
            self.timeslot_entry(t, "Youtube Broadcast").value = "https://youtu.be/{}".format(broadcast_info["id"])
            self.timeslot_entry(t, "Youtube Chat ID").value = broadcast_info["snippet"]["liveChatId"]

    def update_youtube_broadcast_description(self):
        title = self.make_youtube_title()
        description = self.make_youtube_description()
        # Education is category ID 27 in the US (from API explorer)
        category_id = 27
        update_resp = self.auth.youtube.videos().update(
            part="id,snippet,contentDetails,status",
            body={
                "id": self.youtube_broadcast_id(),
                "snippet": {
                    "title": title,
                    "description": description,
                    "categoryId": category_id
                }
            }
        ).execute()

    def render_thumbnail(self, thumbnail_params):
        return thumbnail.render_thumbnail(thumbnail_params["background"],
                thumbnail_params["bold_font"],
                thumbnail_params["regular_font"],
                self.title_card_title(),
                self.title_card_chair(),
                self.title_card_schedule())

    def chat_category_name(self):
        name = re.sub("\-+", "-", re.sub("[^0-9A-Za-z\- ]+", "", self.event.lower()))
        # Max length is 100
        if len(name) > 100:
            return name[0:99].strip()
        return name

    # Channel names have to be lower case without spaces and certain characters (&) so take
    # these out. They also can't have two -- in a sequence so also replace those
    def chat_channel_name(self):
        # Tutorials use just the event name as the channel title to keep the chat in a single
        # place across the breaks. Since we put channels under per-event categories, we just
        # name the tutorial channel within its category as "general"
        if self.timeslot_entry(0, "Event Type").value == "Tutorial" \
            or self.timeslot_entry(0, "Event").value == self.timeslot_entry(0, "Session").value:
                return "general"
        name = re.sub("\-+", "-", re.sub("[^0-9A-Za-z\-]+", "", self.name.lower().replace(" ", "-")))
        # Max length is 100
        if len(name) > 100:
            return name[0:99]
        return name

    def contributor_info_html(self, zoom_meeting_info):
        session_time = self.session_time()
        schedule_html = ""
        chairs = set()
        for t in self.timeslots:
            time = self.day.entry(t, "Time Slot").value
            time_slot_title = self.day.entry(t, "Time Slot Title").value
            presenter = self.day.entry(t, "Contributor(s)").value.replace("|", ", ")
            schedule_html += "<li><b>{}</b>: '{}' presented by {}</li>".format(time, time_slot_title, presenter)
            slot_chairs = self.day.entry(t, "Chair(s)").value
            if slot_chairs:
                for c in slot_chairs.split("|"):
                    chairs.add(c)

        # List two numbers for each country, and the one click phone number.
        # Zoom already sends the numbers sorted by country, so no need to re-group them here
        zoom_call_info = ""
        listed_countries = {number["country"]: 0 for number in zoom_meeting_info["settings"]["global_dial_in_numbers"]}
        for number in zoom_meeting_info["settings"]["global_dial_in_numbers"]:
            if listed_countries[number["country"]] == 1:
                continue
            listed_countries[number["country"]] += 1

            one_click_number = "{},,{}#,,{}#".format(number["number"],
                    self.timeslot_entry(0, "Zoom Meeting ID").value,
                    zoom_meeting_info["pstn_password"]).replace(" ", "")

            zoom_call_info += "<li><b>{}</b>: {} ({})</li><ul><li><b>One-click</b>: {}</li></ul>".format(
                    number["country_name"], number["number"], number["type"], one_click_number)

        # NOTE: You'll want to replace this email content with your own, with links to the corresponding
        # conference webpages and your own schedule instructions.
        return """
            <div style="margin-bottom:.5rem;margin-top:.5rem;">
            <h1>{session_title}</h1>
            <h2>Instructions</h2>
                <p>Please see our guide on <a href="http://ieeevis.org/year/2020/info/presenter-information/presenting-virtually">
                presenting at the virtual conference</a> and watch the relevant example session tutorial to see what to expect as
                a presenter in the virtual conference.</p>
                <p>
                <b>Joining the Zoom Meeting.</b> The Zoom meeting will begin 15 minutes before
                the session starts to allow the contributors, chair, and technician to set up
                and test everyone's audio and video set up. Make sure you are in a well-lit
                and quiet room, and have your laptop plugged in.
                <b>Please make sure your Zoom is updated to version 5.3 or later.</b>
                Follow <a href="https://support.zoom.us/hc/en-us/articles/201362233-Upgrade-update-to-the-latest-version">
                this guide</a> to update Zoom to the latest version.
                The Zoom meeting information is included below in this email.
                </p>
                <p>
                There is a <b>hard cut-off</b> 10 minutes after
                the session is scheduled to end to allow time to set up the next session,
                so please keep to the session schedule.
                Additional discussion after the session can be continued on Discord,
                an invitation and link directly to the channel for this session are included below.
                </p>
            <h2>Session Schedule</h2>
            <ul>
                <li>Session Start: {start}</li>
                <li>Session End: {end}</li>
                <li>Session Chair(s): {chairs}</li>
                <li>Session Website: <a href="https://virtual.ieeevis.org/session_{session_id}.html">Virtual Conference Website</a></li>
                {schedule}
            </ul>
            <h2>Zoom Meeting Information (DO NOT DISTRIBUTE)</h2>
            <ul>
                <li>Meeting URL: <a href="{zoom_url}">{zoom_url}</a></li>
                <li>Meeting ID: <code>{zoom_id}</code></li>
                <li>Meeting Password: <code>{zoom_password}</code></li>
                <li>Call-in Password: <code>{zoom_passcode}</code></li>
                <li>Global Call-in Numbers</li>
                <ul>
                    {zoom_call_info}
                </ul>
            </ul>
            <h2>Youtube Information</h2>
            <ul>
                <li>Youtube URL: <a href="{youtube_url}">{youtube_url}</a></li>
            </ul>
            <h2>Discord Chat Information</h2>
            You can download the Discord app <a href="https://discord.com/">here</a>, or use it in your browser.
            <ul>
                <li>Discord Invitation: <a href="{discord_invite}">{discord_invite}</a></li>
                <li>Discord Channel: <a href="{discord_url}">{discord_url}</a></li>
            </ul>
            </div>
            """.format(session_title=self.event_session_title(),
                    start=format_time(session_time[0]),
                    schedule=schedule_html,
                    end=format_time(session_time[1]), chairs=" ".join(chairs),
                    session_id=self.timeslot_entry(0, "Session ID").value,
                    zoom_url=self.timeslot_entry(0, "Zoom URL").value,
                    zoom_id=self.timeslot_entry(0, "Zoom Meeting ID").value,
                    zoom_password=self.timeslot_entry(0, "Zoom Password").value,
                    zoom_passcode=zoom_meeting_info["pstn_password"],
                    zoom_call_info=zoom_call_info,
                    youtube_url=self.timeslot_entry(0, "Youtube Broadcast").value,
                    discord_invite=self.timeslot_entry(0, "Discord Invite Link").value,
                    discord_url=self.timeslot_entry(0, "Discord Link").value)

    # Email the contributor and chair who will be presenting in the session(s)
    # Multiple sessions can be taken for tutorials, where we only want to send one email
    # to the tutorial organizers with all the information
    # logo_image is the optional byte array of the image to attach and inline at
    # the bottom of the email
    def email_contributors(self, logo_image=None):
        # Collect the list of emails for people in the session
        recipients = set()
        for t in range(self.num_timeslots()):
            emails = []
            if self.timeslot_entry(t, "Contributor Email(s)").value:
                emails = emails + self.timeslot_entry(t, "Contributor Email(s)").value.split("|")

            if self.timeslot_entry(t, "Chair Email(s)").value:
                emails = emails + self.timeslot_entry(t, "Chair Email(s)").value.split("|")

            if self.timeslot_entry(t, "Organizer Email(s)").value:
                emails = emails + self.timeslot_entry(t, "Organizer Email(s)").value.split("|")

            for e in emails:
                recipients.add(e)

        # We need the call in numbers list and the call in passcode, which we don't keep in the sheet
        zoom_meeting_info = self.get_zoom_meeting_info()

        subject = CONFERENCE_NAME + ": {} Contributor and Chair Information".format(self.event_session_title())

        email_body = """<p>Dear Contributor, Chair, or Organizer,</p>
                <p>This email contains information for a
                conference session below in which you are a contributor, chair, or organizer.
                You will receive one such email per-session and/or tutorial. Please contact the tech committee
                with any questions.
                <b>If you are the contact author, but not the presenter, please forward this immediately to the presenting author.</b>""" + \
                self.contributor_info_html(zoom_meeting_info)

        # Generate the ICS calendar event attachment
        calendar = self.make_calendar(with_setup_time=True, zoom_info=zoom_meeting_info)
        event_attachment = MIMEBase("application", "plain")
        event_attachment.set_payload(bytes(str(calendar), "utf8"))
        event_attachment.add_header("Content-Disposition", "attachment",
                filename="{}.ics".format(self.event_session_title()))
        attachments = [event_attachment]

        # Attach the image logo if we have one
        if logo_image:
            attach_img = MIMEImage(logo_image)
            attach_img.add_header("Content-Disposition", "inline", filename="logo_image.png")
            attach_img.add_header("Content-ID", "<logo_image>")
            attachments.append(attach_img)
            email_body += "<img width='400' src='cid:logo_image' alt='Logo'/>"

        alternative_text = """{schedule}
        Zoom URL: {zoom_url}
        Zoom ID: {zoom_id}
        Zoom password: {zoom_password}
        Discord URL: {discord_url}""".format(schedule=str(self),
            zoom_url=self.timeslot_entry(0, "Zoom URL").value,
            zoom_id=self.timeslot_entry(0, "Zoom Meeting ID").value,
            zoom_password=self.timeslot_entry(0, "Zoom Password").value,
            discord_url=self.timeslot_entry(0, "Discord Link").value)

        send_html_email(subject, email_body, list(recipients), self.auth.email,
                alternative_text=alternative_text, attachments=attachments)
        return len(recipients)

    def discord_embed_dict(self):
        embed = base_discord_embed()
        embed["title"] = "Schedule for {}".format(self.event_session_title())

        # TODO: Manage Discord Embed size limits: https://discordjs.guide/popular-topics/embeds.html#embed-limits
        # Will we actually hit these? Can finish test scheduling the week and if not, ignore it for now
        if self.timeslot_entry(0, "Youtube Broadcast").value:
            embed["description"] = "Youtube URL: " + self.timeslot_entry(0, "Youtube Broadcast").value

        session_time = self.session_time()
        embed["fields"].append({
            "name": "Start",
            "value": format_time(session_time[0]),
            "inline": True
        })
        embed["fields"].append({
            "name": "End",
            "value": format_time(session_time[1]),
            "inline": True
        })

        if self.timeslot_entry(0, "Chair(s)").value:
            embed["fields"].append({
                "name": "Session Chair(s)",
                "value": self.timeslot_entry(0, "Chair(s)").value.replace("|", ", "),
                "inline": False
            })

        for t in self.timeslots:
            time = self.day.entry(t, "Time Slot").value
            time_slot_title = self.day.entry(t, "Time Slot Title").value
            presenter = self.day.entry(t, "Contributor(s)").value.replace("|", ", ")
            field_value = time_slot_title
            if self.day.entry(t, "Authors").value:
                authors = self.day.entry(t, "Authors").value.replace("|", ", ")
                field_value += " by {}.\nPresented by {}".format(authors, presenter)
            else:
                field_value += " by " + presenter

            embed["fields"].append({
                "name": time,
                "value": field_value,
                "inline": False
            })
        return embed

    # Generate a public-facing calendar item which does not include the Zoom information
    def make_calendar(self, with_setup_time=False, zoom_info=None):
        calendar = ics.Calendar()
        event = ics.Event()
        session_time = self.session_time()
        event.begin = session_time[0]
        if with_setup_time:
            event.begin -= self.setup_time()
        event.end = session_time[1]
        event.name = self.event_session_title()
        event.description = ""
        # We include the zoom info in the calendar file sent to presenters,
        # put the URL up front in ICS because google calendar limits the length of this
        if zoom_info:
            event.description += "Zoom URL: " + self.timeslot_entry(0, "Zoom URL").value + \
                    "\nZoom Meeting ID: " + self.timeslot_entry(0, "Zoom Meeting ID").value + \
                    "\nZoom Password: " + self.timeslot_entry(0, "Zoom Password").value + "\n"

        event.description += str(self)
        calendar.events.add(event)
        return calendar

    def title_card_title(self):
        session_time = self.session_time()
        pretty_session_time = format_time_slot(session_time[0], session_time[1])
        pretty_session_time = pretty_session_time[0:2] + ":" + pretty_session_time[2:4] + \
                "-" + pretty_session_time[5:7] + ":" + pretty_session_time[7:9]
        return "{}: {}".format(self.event_session_title(), pretty_session_time)

    def title_card_chair(self):
        if self.timeslot_entry(0, "Event Type").value == "Tutorial":
            return ""

        chairs = set()
        for i in range(self.num_timeslots()):
            if self.timeslot_entry(i, "Chair(s)").value:
                slot_chairs = self.timeslot_entry(i, "Chair(s)").value.split("|")
                for c in slot_chairs:
                    chairs.add(c)
        return "Chair(s): {}".format(", ".join(list(chairs)))

    def title_card_schedule(self):
        schedule_text = ""
        for t in range(len(self.timeslots)):
            presentation_time = self.timeslot_entry(t, "Time Slot").value
            presentation_title = self.timeslot_entry(t, "Time Slot Title").value
            schedule_text += "{}: {}\n".format(presentation_time[0:2] + ":" + presentation_time[2:4],
                    presentation_title)

            if self.timeslot_entry(t, "Authors").value:
                schedule_text += "  {}".format(self.timeslot_entry(t, "Authors").value.replace("|", ", "))
            else:
                schedule_text += "  {}".format(self.timeslot_entry(t, "Contributor(s)").value.replace("|", ", "))

            if t + 1 < len(self.timeslots):
                schedule_text += "\n"
        return schedule_text

    def __str__(self):
        # Note: Does not and should not include Zoom info, this is posted on Youtube and the
        # publicly shared calendar file.
        session_time = self.session_time()
        text = CONFERENCE_NAME + ": " + self.event_session_title()
        if self.timeslot_entry(0, "Event URL").value:
            text += "\nEvent Webpage: {}".format(self.timeslot_entry(0, "Event URL").value)

        # NOTE: You'll want to replace this with the link to your conference session page
        text += "\nSession Webpage: https://virtual.ieeevis.org/session_{}.html".format(self.timeslot_entry(0, "Session ID").value)

        text += "\nSession start: " + format_time(session_time[0]) + \
                "\nSession end: " + format_time(session_time[1])
        if self.timeslot_entry(0, "Youtube Broadcast").value:
            text += "\nYoutube URL: " + self.timeslot_entry(0, "Youtube Broadcast").value

        if self.timeslot_entry(0, "Discord Link").value:
            text += "\nDiscord Link: " + self.timeslot_entry(0, "Discord Link").value

        if self.timeslot_entry(0, "Chair(s)").value:
            text += "\nSession Chair(s): " + self.timeslot_entry(0, "Chair(s)").value.replace("|", ", ")

        for t in self.timeslots:
            time = self.day.entry(t, "Time Slot").value
            time_slot_title = self.day.entry(t, "Time Slot Title").value
            if self.day.entry(t, "Contributor(s)").value:
                presenter = self.day.entry(t, "Contributor(s)").value.replace("|", ", ")
                if self.day.entry(t, "Authors").value:
                    authors = self.day.entry(t, "Authors").value.replace("|", ", ")
                    text += "\n    {}: {} by {}.\n    Presented by {}".format(time, time_slot_title, authors, presenter)
                else:
                    text += "\n    {}: {} by {}".format(time, time_slot_title, presenter)
            else:
                text += "\n    {}: {}".format(time, time_slot_title)
        return text

