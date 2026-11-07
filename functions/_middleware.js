// Send the bare domain to www so search engines see one canonical host.
export async function onRequest({ request, next }) {
  const url = new URL(request.url);
  if (url.hostname === "timelyreach.com") {
    url.hostname = "www.timelyreach.com";
    return Response.redirect(url.toString(), 301);
  }
  return next();
}
