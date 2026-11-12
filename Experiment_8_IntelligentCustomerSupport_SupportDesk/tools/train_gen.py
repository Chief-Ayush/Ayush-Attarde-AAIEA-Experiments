"""Training data for the learned classifier: phrase templates written separately from the gold set (different wording, same product)."""
import csv
import random

L, B, T, F, G, U = "Login / Access", "Billing", "Technical Error", "Feature Request", "General Information", "Unresolved Issue"
PRE = ["", "", "", "Hi, ", "Hello team, ", "Quick question: ", "Hi support, ", "Good morning. ", "Hey, ", "Hello, I need help. ", "Dear support, ", "Urgent? Not sure, but "]
POST = ["", "", "", " Thanks.", " Thank you!", " Please advise.", " Any help appreciated.", " Regards, Sam", " Cheers"]

# (intent, complexity, priority, article, route, [core phrases])
GROUPS = [
 (L, "Low", "P3", "KB001", "AUTO", ["I do not remember my password", "how can I set a new password", "forgot the password for my TaskNest login", "the password reset mail is missing", "my reset link is not working", "I want to reset the password of my account", "can't recall my sign-in password, what now", "where is the forgot password option"]),
 (L, "Low", "P3", "KB002", "AUTO", ["my account is locked", "I am locked out after entering the wrong password", "how do I unlock my account", "account says too many attempts, try later", "locked out for fifteen minutes, can I speed that up", "login blocked because of failed attempts"]),
 (L, "Medium", "P2", "KB003", "REV", ["my two-factor codes are not accepted", "I do not have my 2FA device anymore", "authenticator app was wiped from my phone", "need 2FA turned off because the verification code never comes", "lost the second factor, how do I get in", "backup codes are gone and I cannot pass verification"]),
 (L, "Medium", "P2", None, "REV", ["I cannot log in to my workspace and need it today", "login page keeps refreshing without letting me in", "our team lead cannot access the workspace", "sign in fails with unknown error for the whole finance group", "I cannot reach my account after changing my email", "invitation link for the new colleague does not work", "how to configure single sign-on for our company", "need SAML login set up with our identity provider"]),
 (B, "Low", "P3", "KB004", "AUTO", ["where can I see my invoices", "I need last quarter's invoice as a PDF", "send me the receipt for the payment", "how do I get the billing history", "download invoice for accounting", "can I get a copy of our invoice for March"]),
 (B, "Low", "P3", "KB005", "AUTO", ["how do I replace the credit card on file", "our card has expired, where do I add a new one", "change payment method please", "update the card used for billing", "which payment options can I use", "I want to use a different card"]),
 (B, "Low", "P3", "KB006", "AUTO", ["how can I cancel the subscription", "we want to upgrade our plan", "I would like to switch to a smaller plan", "stop our subscription at the end of the month", "how do I change plans", "can I downgrade next billing cycle"]),
 (B, "Medium", "P2", "KB016", "REV", ["I see two charges for the same invoice", "please refund the last payment", "I was billed twice this month", "we got overcharged, please check", "I want my money back for the annual plan", "there is a duplicate charge on my card"]),
 (B, "Medium", "P2", None, "REV", ["the payment failed and my workspace is suspended", "invoice total is wrong for our number of seats", "I was charged during the free trial", "my bank declined the payment but the card is fine", "the invoice lists a plan we do not use", "we need the invoice in another currency", "need tax details on the invoice for the audit"]),
 (T, "Medium", "P2", "KB007", "REV", ["exporting a report gives an internal server error", "the export button fails with an error message", "CSV export never finishes and times out", "PDF export creates a blank page", "exports are failing for the big project", "server error whenever I export the board"]),
 (T, "Medium", "P3", "KB008", "REV", ["the app is very slow", "pages are loading slowly since yesterday", "everything lags when I open big boards", "performance has gotten much worse for me", "loading screen spins for minutes", "TaskNest feels sluggish on my laptop"]),
 (T, "Medium", "P3", "KB009", "REV", ["calendar sync stopped working", "Slack notifications are no longer posted", "the integration with Google Calendar lost connection", "synced events show the wrong day", "integration keeps disconnecting every few days", "our Slack channel gets no updates from TaskNest"]),
 (T, "Medium", "P3", None, "REV", ["file upload fails for large attachments", "I get an error 500 on the timeline page", "the mobile app closes by itself when I open a project", "comments vanish after reloading", "automation rule runs twice", "search does not find new tasks", "email notifications are delayed by hours", "API calls are rate limited too early", "board drag and drop is broken in my browser", "weekly time report has wrong totals"]),
 (T, "High", "P1", "KB014", "URG", ["the whole service is down for all users", "outage: nobody in our company can open TaskNest", "application is not reachable for any of our employees", "site is down for every team, please escalate", "complete service unavailable since this morning", "all our users see a gateway error", "no one in the organisation can use the product", "everything is down, entire company is blocked"]),
 (T, "High", "P1", None, "URG", ["we believe there has been a data breach in our workspace", "someone unknown accessed our account and downloaded data", "my account was compromised and I see strange activity", "a stranger was added as workspace admin", "our API token leaked on a public repository", "security incident: private project visible to outsiders", "suspicious login from a country we do not operate in", "employees replied to a phishing mail pretending to be you", "unauthorised changes were made to our billing owner", "I think someone hacked our workspace"]),
 (F, "Low", "P4", "KB013", "FEED", ["please add a dark theme", "I would like to suggest a calendar view with weeks", "could you add a feature for task dependencies", "feature request: templates for recurring projects", "is a native Zoom integration coming", "it would help to have bulk actions on cards", "new feature idea: custom statuses with colours", "do you plan to add an offline mode", "suggestion: reminders by SMS", "could the sidebar be made smaller"]),
 (G, "Low", "P4", "KB010", "AUTO", ["what does the Pro plan include", "how many projects are in the Basic plan", "difference between the plans", "is audit export part of Business", "what are the limits on the free plan", "do I get unlimited members with Business"]),
 (G, "Low", "P4", "KB011", "AUTO", ["which web browsers are supported", "do you have an iPhone app", "is TaskNest available for Android", "can I run it in Firefox", "are there mobile apps", "what operating systems does the app support"]),
 (G, "Low", "P4", "KB012", "AUTO", ["can I move my Trello board into TaskNest", "how to import tasks from a CSV file", "do you support importing from Asana", "what is the largest import allowed", "bring existing tasks from a spreadsheet", "migrating from another tool, is there an importer"]),
 (G, "Low", "P4", "KB015", "AUTO", ["how do I add a teammate", "what is the difference between admin and member", "can guests see all projects", "who can invite people to a workspace", "how do roles work", "give a coworker more permissions"]),
 (G, "Medium", "P3", None, "REV", ["is there a discount for charities", "where do you host our data", "can we sign a DPA", "send us your security whitepaper for procurement", "what uptime do you commit to", "do you offer training sessions for teams", "how long are deleted items kept", "is there a pricing option for universities", "how should we organise projects across regions", "what compliance certifications do you hold"]),
 (U, "High", "P2", None, "REV", ["I tried the steps you sent and the problem remains", "your suggestion did not fix it", "still not resolved after following the article", "this is the third time I report this issue", "same error again after your fix", "nothing helped, issue still open", "the workaround did not work for us", "it came back after you closed my ticket", "I already did everything the guide says", "problem persists even after reinstalling"]),
 (T, "Medium", "P3", None, "INFO", ["it is not working", "help me please", "something is wrong", "not working for me", "there is a problem", "error on my screen", "please check my issue", "this is broken", "can you look into this", "I need assistance"]),
]

GROUPS += [
 (G, "Low", "P4", "KB010", "AUTO", ["what is the monthly price of the Business plan", "how much does Basic cost per user", "cost of the Pro tier", "do you charge per member on Pro"]),
 (F, "Low", "P4", "KB013", "FEED", ["I wish the board had a compact layout", "would love a different theme option", "wish there were templates for sprint planning"]),
 (L, "Low", "P3", "KB001", "AUTO", ["can't remember my login password, how do I recover it", "the email with the new password link has not come through", "the link to choose a new password is broken", "I need a fresh password for my account"]),
 (L, "Low", "P3", "KB002", "AUTO", ["I got blocked from signing in after a few wrong tries", "when does the sign-in block end", "how soon can I try logging in again after being locked"]),
 (L, "Medium", "P2", "KB003", "REV", ["my phone with the authenticator was stolen", "two step verification codes are invalid", "turn off two step verification for my user"]),
 (L, "Medium", "P2", None, "REV", ["an invited teammate cannot accept the invite", "I get redirected back to the sign in screen repeatedly", "setting up Okta single sign-on for our staff", "none of us can enter the workspace and a client demo is today"]),
 (B, "Low", "P3", "KB004", "AUTO", ["can I get a receipt for our records", "where do I see past invoices", "pdf of the invoice for the finance team", "I'd like the invoice for the quarter"]),
 (B, "Low", "P3", "KB005", "AUTO", ["swap the payment card on our account", "the card on file is outdated", "add a new credit card for future payments"]),
 (B, "Low", "P3", "KB006", "AUTO", ["end my plan", "how to switch from Pro to Business", "move to a lower tier next cycle", "when will the new plan start after I upgrade"]),
 (B, "Medium", "P2", "KB016", "REV", ["an extra payment was taken from our card", "billed more than agreed in the quote", "ask for a refund since we did not use the product", "the amount charged is twice what I expected"]),
 (B, "Medium", "P2", None, "REV", ["payment did not go through and we lost write access", "the invoice shows the wrong company name", "we need invoices in a different currency", "billing contact change request with a tax number"]),
 (T, "Medium", "P2", "KB007", "REV", ["report download throws a server error", "export to csv hangs forever", "exported pdf has no content"]),
 (T, "Medium", "P3", "KB008", "REV", ["very laggy this afternoon", "a huge project takes ages to open", "scrolling is choppy on big lists"]),
 (T, "Medium", "P3", "KB009", "REV", ["Slack messages from TaskNest stopped arriving", "the calendar integration shows events a day late", "two-way sync with Outlook calendar broke"]),
 (T, "Medium", "P3", None, "REV", ["uploading big files fails", "the calendar page returns a 500 error", "app crashes on startup on my phone", "my edits revert after refresh", "webhook calls fire twice", "tasks are missing from the search index", "exported hours do not add up"]),
 (T, "High", "P1", "KB014", "URG", ["TaskNest is not loading for any of our staff", "complete downtime affecting our entire organisation", "everyone gets a 503 error", "the platform is unreachable for all our teams"]),
 (T, "High", "P1", None, "URG", ["an unknown person has admin rights in our workspace", "credentials were shared with someone suspicious and now strange activity appears", "our private data is exposed to another customer", "unauthorised export of all projects happened overnight", "we discovered a leaked access token"]),
 (F, "Low", "P4", "KB013", "FEED", ["would be nice to have colours for custom fields", "request: reminder emails for overdue tasks", "can you support time estimates in hours and minutes", "please add a home screen widget", "add the ability to pin projects", "I wish tasks could be linked to documents", "any chance of a calendar sync for subtasks being added", "we need a read-only share link feature, please consider it"]),
 (G, "Low", "P4", "KB010", "AUTO", ["is the Business plan limited by members", "what does Pro offer compared to Basic", "which tier has SSO"]),
 (G, "Low", "P4", "KB011", "AUTO", ["does it work on tablets", "supported operating systems for the app", "is there a desktop client"]),
 (G, "Low", "P4", "KB012", "AUTO", ["can I upload tasks from Excel", "moving from Jira to TaskNest, is that possible", "how do I load a backlog from another system"]),
 (G, "Low", "P4", "KB015", "AUTO", ["how do I give someone admin rights", "who can remove members", "can a member see all projects"]),
 (G, "Medium", "P3", None, "REV", ["do you have a nonprofit price", "where are your servers located", "can you sign our data protection addendum", "send security details for vendor review", "what uptime percentage do you guarantee", "how long do you retain backups"]),
 (U, "High", "P2", None, "REV", ["your fix did not help", "the article you pointed to did not solve it", "I wrote twice already about this and still no solution", "still seeing the same error after the workaround", "no change after following all instructions"]),
 (T, "Medium", "P3", None, "INFO", ["problem", "doesn't work", "something happened", "need help urgently", "please assist", "an issue again"]),
]


def build(seed=11):
    rnd = random.Random(seed)
    rows = []
    for intent, cx, pri, art, route, cores in GROUPS:
        reps = 3 if route != "URG" else 4
        for core in cores:
            for _ in range(reps):
                pre, post = rnd.choice(PRE), rnd.choice(POST)
                t = pre + (core[0].upper() + core[1:] if not pre or pre.endswith((". ", ": ")) else core) + ("." if not core.endswith("?") else "") + post
                rows.append((t.strip(), intent, cx, pri, art or "", route))
    rnd.shuffle(rows)
    return list(dict.fromkeys(rows))


if __name__ == "__main__":
    rows = build()
    with open("data/train_queries.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["text", "intent", "complexity", "priority", "article", "route"])
        w.writerows(rows)
    print(len(rows))
