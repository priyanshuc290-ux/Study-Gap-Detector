/*
 * Entry point. The pieces load in this order (see index.html):
 *   report.js  talks to the backend for the AI study gap report and streaming
 *   core.js    helpers, API client, login state, router
 *   quiz.js    Study Gap Checker (Mode 1)
 *   chat.js    full-screen Ask AI Agent
 *   pages.js   home, dashboard, login, sign up, contact, reviews, about, admin
 * NEVER put API keys in frontend code: the backend holds them.
 */
SG.start();
