from core.auth import Authentication
from core.templates import load_templates_dict
from core.papers_db import PapersDatabase
from core.aws_email import send_aws_email_paper

import argparse
import time


def send_emails_to_authors(auth: Authentication, papers_csv_file: str, event_prefix: str, email_template: str, uid: str = None):
    """send email for papers .
    authentication: Authentication instance in which aws ses client was authenticated
    papers_csv_file: path to papers db file
    event_prefix: event prefix to send to, can only do one at a time

    """
    papersDb = PapersDatabase(papers_csv_file)
    templates = load_templates_dict()
    if email_template == "upload_request":
        if event_prefix == "a-vast-challenge" or event_prefix == "a-scivis-contest" or event_prefix == "w-mercado"  or event_prefix == "w-visxai":
            template = templates["upload_request_associated_event_workshop_late"]
        elif event_prefix == "a-ldav" or event_prefix == "a-vizsec" or event_prefix == "a-vds" or event_prefix == "a-visap" or event_prefix == "w-vis4dh" or event_prefix == "w-topoinvis" or event_prefix == "w-energyvis" or event_prefix == "w-vis4good" or event_prefix == "w-eduvis" or event_prefix == "w-visxvision" or event_prefix == "w-vahc" or event_prefix == "w-visxprov" or event_prefix == "a-biomedchallenge" or event_prefix == "w-altvis" or event_prefix == "w-viscomm" or event_prefix == "w-cityvis" or event_prefix == "w-nlviz" or event_prefix == "s-vds" or event_prefix == "w-vis4pandemres":
            template = templates["upload_request_associated_event_workshop"]
        elif event_prefix == "v-siggraph" or event_prefix == "v-ismar" or event_prefix == "v-vr":
            template = templates["upload_request_vr_ismar"]
        elif event_prefix == "v-short" or event_prefix == "v-full" or event_prefix == "v-cga":
            template = templates["upload_request_full_short"]
    elif email_template == "missing_preview":
        if event_prefix == "v-cga" or event_prefix == "v-tvcg":
            template = templates["missing_preview_tvcg_cga"]
        else:
            template = templates["missing_preview"]
    elif email_template == "missing_urgent":
        if event_prefix == "v-cga" or event_prefix == "v-tvcg" or event_prefix == "v-short" or event_prefix == "v-full":
            template = templates["missing_urgent"]
        else:
            template = templates["missing_urgent_workshop"]
    elif email_template == "presentation_tips":
        template = templates["presentation_tips"]
    elif email_template == "copyright_delay":
        template = templates["copyright_delay"]
    elif email_template == "missing_video":
        template = templates["missing_video"]
    elif email_template == "reminder_survey":
        template = templates["reminder_survey"]

    if uid is not None:
        papers = list(filter(lambda p: p["UID"] == uid, papersDb.data))
    elif event_prefix is not None:
        papers = list(
            filter(lambda p: p["Event Prefix"] == event_prefix, papersDb.data))

    print(f"{len(papersDb.data)} total papers loaded, filtered for {event_prefix}, for which {len(papers)} papers will be processed.")

    for i in range(len(papers)):
        paper = papers[i]
        print(f"paper {i+1}/{len(papers)} {paper['UID']}")
        response = send_aws_email_paper(auth, paper, template)
        print(f"    {response}")
        if i % 4 == 3:
            print(f"    saved. Waiting 2s...")
            time.sleep(2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Script to email paper authors to request files via dropbox.')

    parser.add_argument('--verify', help='verify email identity for no-reply@ieeevis.org',
                        action='store_true', default=False)
    parser.add_argument('--send', help='send upload request emails for paper db and event-prefix',
                        action='store_true', default=False)

    parser.add_argument(
        '--papers_csv_file', help='path to papers db CSV file', default="ieeevis_papers_db.csv")
    parser.add_argument(
        '--event_prefix', help='filter papers that match the event prefix', default=None)
    parser.add_argument(
        '--uid', help='filter papers that match this UID', default=None)
    parser.add_argument(
        '--email_template', help='template to use for the email (e.g., \"upload_request\", \"missing_preview\")', default="upload_request")

    args = parser.parse_args()
    auth = Authentication(email=True)

    if args.verify:
        # verify an email account
        acc = auth.email.verify_email_identity(
            EmailAddress='tech@ieeevis.org')
        print(acc)
    elif args.send:
        # send emails only if event_prefix provided, never send all db
        if args.event_prefix is not None:
            send_emails_to_authors(
                auth, args.papers_csv_file, args.event_prefix, args.email_template, args.uid)
