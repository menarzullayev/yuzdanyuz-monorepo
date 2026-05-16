import os


class ForceScriptNameMiddleware:
    """
    Sub-path deployment uchun SCRIPT_NAME'ni majburiy o'rnatadi va kelgan
    PATH_INFO'dan prefiksni qirqadi.

    Faqat `FORCE_SCRIPT_NAME` env mavjud bo'lganda faollashadi (PHP proxy yoki
    nginx subpath uchun). Lokal/direct gunicorn ishga tushirishda no-op.

    `request.META` ishlatamiz (WSGI + ASGI ikkalasi qo'llab-quvvatlaydi);
    `request.environ` faqat WSGI'da bor — Daphne'da AttributeError beradi.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.script_name = os.getenv('FORCE_SCRIPT_NAME', '').rstrip('/')

    def __call__(self, request):
        if not self.script_name:
            return self.get_response(request)

        request.META['SCRIPT_NAME'] = self.script_name
        prefix = self.script_name + '/'
        if request.path_info.startswith(prefix):
            request.path_info = request.path_info[len(self.script_name) :]

        return self.get_response(request)
