import json

def load_templates_dict() -> dict:
    """Loads template strings dict from 'template_strings.json'.
    Should be hierarchy of dict->dict>values based on context.
    Important: Curly braces '{}' in template strings will be replaced with corresponding values using str.format

    For instance
    {
        "paper_dropbox_requests" : {
            "title" : "...",
            "description" : "..."
        },
        "email_template_1" : {
            "sender" : "",
            "recipient" : "",
            "subject" : "",
            "body_text" : "",
            "body_html" : ""
        }
    }
    """
    with open("template_strings.json", "r", encoding='utf-8') as f:
        dct = json.load(f, )
        return dct