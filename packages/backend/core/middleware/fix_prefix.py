class ForceScriptNameMiddleware:
    """
    Djangoning muhitiga SCRIPT_NAME ni majburiy kiritadi.
    Bu PHP Proxy orqali sub-path'da ishlashda redirect loopni to'xtatadi
    va URL matchingni (404-siz) ta'minlaydi.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Djangoga uning prefiksi haqida xabar beramiz
        request.environ['SCRIPT_NAME'] = '/yuzdanyuz'
        
        # Agar PATH_INFO prefiks bilan kelsa, uni qirqib tashlaymiz (ehtiyot chorasi)
        if request.path_info.startswith('/yuzdanyuz/'):
            request.path_info = request.path_info[len('/yuzdanyuz')-1:]
            
        return self.get_response(request)
