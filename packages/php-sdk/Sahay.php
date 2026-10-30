<?php
/**
 * Sahay PHP SDK (single file, PHP 7.4+): for businesses on PHP / WordPress / Laravel / custom stacks.
 *
 *   $sahay = new Sahay(getenv('SAHAY_SECRET_KEY'));
 *   $id = $sahay->identify(['email' => $user->email]);           // ['user' => ..., 'userHash' => ...] for the widget
 *   $sahay->sendEvent('order.shipped', [...]);                     // level 2
 *   $sahay->handle(['getOrder' => fn($p) => ..., 'checkStock' => fn($p) => ...]);   // level 3 endpoint
 */
class Sahay
{
    const DEFAULT_HOST = 'https://sahay-536440059442.asia-south1.run.app';
    const TOLERANCE_S = 300;

    private $secretKey;
    private $host;

    public function __construct(string $secretKey, string $host = self::DEFAULT_HOST)
    {
        if (strpos($secretKey, 'sk_') !== 0) throw new InvalidArgumentException('Sahay: pass your secret key (sk_...)');
        $this->secretKey = $secretKey;
        $this->host = rtrim($host, '/');
    }

    public function identify(array $user): array
    {
        $value = strtolower(trim($user['email'] ?? $user['phone'] ?? ''));
        if ($value === '') throw new InvalidArgumentException('identify needs an email or phone');
        return ['user' => $user, 'userHash' => hash_hmac('sha256', $value, $this->secretKey)];
    }

    public function sendEvent(string $type, array $data, ?string $id = null): array
    {
        $id = $id ?: $type . ':' . ($data['orderRef'] ?? $data['sku'] ?? bin2hex(random_bytes(8))) . ':' . ($data['status'] ?? '');
        return $this->request('POST', '/v1/api/events', ['id' => $id, 'type' => $type, 'data' => $data]);
    }

    public function createTicket(array $customer, string $message, ?string $subject = null, ?string $orderRef = null): array
    {
        return $this->request('POST', '/v1/api/tickets', ['customer' => $customer, 'message' => $message, 'subject' => $subject, 'order_ref' => $orderRef]);
    }

    public function createLead(array $lead): array
    {
        return $this->request('POST', '/v1/api/leads', $lead);
    }

    public function request(string $method, string $path, ?array $body = null): array
    {
        $ch = curl_init($this->host . $path);
        curl_setopt_array($ch, [
            CURLOPT_CUSTOMREQUEST => $method, CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 15,
            CURLOPT_HTTPHEADER => ['Authorization: Bearer ' . $this->secretKey, 'Content-Type: application/json'],
            CURLOPT_POSTFIELDS => $body === null ? null : json_encode($body),
        ]);
        $out = curl_exec($ch);
        $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);
        $data = json_decode($out ?: '{}', true) ?: [];
        if ($code >= 300) throw new RuntimeException('Sahay ' . $code . ': ' . ($data['detail'] ?? $out));
        return $data;
    }

    public static function verifyWith(string $secret, string $raw, array $headers): bool
    {
        $h = array_change_key_case($headers, CASE_LOWER);
        $ts = $h['x-sahay-timestamp'] ?? null;
        $sig = $h['x-sahay-signature'] ?? null;
        if (!$ts || !$sig || abs(time() - (int)$ts) > self::TOLERANCE_S) return false;
        return hash_equals('sha256=' . hash_hmac('sha256', $ts . '.' . $raw, $secret), $sig);
    }

    public function verify(string $raw, array $headers): bool
    {
        return self::verifyWith($this->secretKey, $raw, $headers);
    }

    /** Answer Commerce Contract calls on this URL. Implement only the actions you support. */
    public function handle(array $actions): void
    {
        $raw = file_get_contents('php://input');
        $headers = function_exists('getallheaders') ? getallheaders() : [];
        header('Content-Type: application/json');
        if (!$this->verify($raw, $headers)) { http_response_code(401); echo json_encode(['ok' => false, 'error' => 'bad_signature']); return; }
        $msg = json_decode($raw, true);
        if (($msg['action'] ?? '') === 'ping') { echo json_encode(['ok' => true, 'data' => ['actions' => array_keys($actions)]]); return; }
        $fn = $actions[$msg['action'] ?? ''] ?? null;
        if (!$fn) { http_response_code(404); echo json_encode(['ok' => false, 'error' => 'not_supported']); return; }
        try {
            echo json_encode(['ok' => true, 'data' => $fn($msg['params'] ?? [])]);
        } catch (Throwable $e) {
            http_response_code(500);
            echo json_encode(['ok' => false, 'error' => $e->getMessage()]);
        }
    }
}
