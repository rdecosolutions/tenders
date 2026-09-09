// Service worker: the part of the site that keeps running after you close
// the tab. Without one, the browser has nowhere to deliver a push.

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('push', event => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch (_) {}
  const title = d.title || 'New Tamil Nadu tenders';
  event.waitUntil(self.registration.showNotification(title, {
    body: d.body || 'Tap to see what changed.',
    icon: 'icon-192.png',
    badge: 'icon-192.png',
    tag: 'tn-tenders',        // replaces the previous night's notification
                              // instead of stacking one per day
    renotify: true,
    data: {url: d.url || '/'}
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  // Focus an already-open tab if there is one, rather than opening a new
  // one every time a notification is tapped.
  event.waitUntil(clients.matchAll({type: 'window', includeUncontrolled: true})
    .then(list => {
      for (const c of list) {
        if (c.url.includes(self.location.host) && 'focus' in c) return c.focus();
      }
      return clients.openWindow(url);
    }));
});
