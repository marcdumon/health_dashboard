# Google Health setup

The Fitbit card reads your Fitbit data through the Google Health API. For this it needs a Google Cloud project with an
OAuth client. You set it up once. Use the Google account of the Google Health app on your phone.

If you already have a Google Cloud project with an OAuth client of type Desktop (for example the one of the Automation
desk), you can use it: enable the API (step 2), add the scopes (step 3) and copy its `client_secret.json` (step 4).

All console links open the project that is selected at the top of the console. Check that it is the right project.

## 1. Make a project

1. Open [Create a project](https://console.cloud.google.com/projectcreate).
2. Give it a name, then click **Create**.
3. Select the new project at the top of the console.

## 2. Enable the API

Open [Google Health API](https://console.developers.google.com/apis/library/health.googleapis.com) and click **Enable**.
The search in the API Library may not find the Google Health API. Use the link.

## 3. Set up the Google Auth platform

1. [Branding](https://console.developers.google.com/auth/branding): set an app name, your email as the user support
   email and as the developer contact. Click **Save**.
2. [Audience](https://console.developers.google.com/auth/audience):
   - User type: **External**.
   - Publishing status: click **Publish app**, so that it shows **In production**.
     In **Testing**, the login stops working after 7 days.
3. [Data access](https://console.developers.google.com/auth/scopes): click **Add or remove scopes**. Paste these scopes
   under **Manually add scopes**, then click **Update** and **Save**:
   - `https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly`
   - `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly`
   - `https://www.googleapis.com/auth/googlehealth.sleep.readonly`

The app is not verified by Google. That is no problem for your own use: an unverified app can have up to 100 users.

## 4. Make the OAuth client

1. Open [Clients](https://console.developers.google.com/auth/clients) and click **Create client**.
2. Application type: **Desktop app**. Give it a name, then click **Create**.
3. Click **Download JSON**.
4. Save the file in this project as `secrets/client_secret.json`.

The `secrets/` folder is in `.gitignore`. Never put the client file or a token anywhere else, and never commit them.

## 5. Log in

Before the first login:
- Sign in to the Google Health app on your phone with the same Google account.
- Wear the tracker at least one day and one night, so there is data to read.

Then press **Log in to Google Health** on the Fitbit card:

1. Your browser opens the Google login. Choose your account.
2. Google shows "Google hasn't verified this app". Click **Advanced**, then **Go to** your app name.
3. Tick every box, then click **Continue**.

The token goes to `secrets/google_health_token.json`. Then press **Download Fitbit**.

## Problems

| You see | Do this |
|---|---|
| `Google login expired` | In [Audience](https://console.developers.google.com/auth/audience), check that the status is **In production**. Then press Log in to Google Health again. |
| `Not granted: ...` | Log in again and tick every box. |
| `... API has not been used in project ... before or it is disabled` | Enable the API with the link in step 2. Wait a few minutes. |
| `No OAuth client secret at ...` | Do step 4. |
| `ACCOUNT_NOT_LINKED` | Sign in to the Google Health app on your phone with the same Google account. |

## More

- [Google Auth platform](https://support.google.com/cloud/answer/15544987)
- [Google Health API setup](https://developers.google.com/health/setup)
- [Google Health API scopes](https://developers.google.com/health/scopes)
