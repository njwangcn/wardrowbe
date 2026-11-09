'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useSession } from 'next-auth/react';
import {
  Camera,
  Download,
  Image as ImageIcon,
  Loader2,
  RefreshCw,
  Shirt,
  Sparkles,
  Upload,
  X,
  AlertCircle,
  CheckCircle2,
  Settings2,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { api, ApiError, getAccessToken } from '@/lib/api';
import { applySessionToken } from '@/lib/hooks/use-session-token';
import { useItems } from '@/lib/hooks/use-items';
import type { Item } from '@/lib/types';
import { cn } from '@/lib/utils';

interface TryOnStatus {
  enabled: boolean;
  configured: boolean;
  model: string;
  mode: string;
  base_url_host: string | null;
}

interface TryOnResult {
  image_base64: string;
  mime_type: string;
  model: string;
  mode: string;
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result);
      resolve(result.includes(',') ? result.split(',', 1)[1] ?? result : result);
    };
    reader.onerror = () => reject(new Error('读取图片失败'));
    reader.readAsDataURL(file);
  });
}

async function urlToBase64(url: string): Promise<string> {
  const token = getAccessToken();
  const resp = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    credentials: 'include',
  });
  if (!resp.ok) throw new Error('读取衣橱图片失败');
  const blob = await resp.blob();
  const dataUrl: string = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error('转换图片失败'));
    reader.readAsDataURL(blob);
  });
  return dataUrl.split(',')[1] ?? dataUrl;
}

function ImageDrop({
  title,
  hint,
  dataUrl,
  onPick,
  onClear,
  icon: Icon,
}: {
  title: string;
  hint: string;
  dataUrl: string | null;
  onPick: (f: File) => void;
  onClear: () => void;
  icon: typeof Camera;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <Card className="flex-1 min-w-[240px]">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <Icon className="h-4 w-4" /> {title}
        </CardTitle>
        <CardDescription className="text-xs">{hint}</CardDescription>
      </CardHeader>
      <CardContent>
        <input
          ref={inputRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onPick(f);
            e.target.value = '';
          }}
        />
        {dataUrl ? (
          <div className="relative">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={dataUrl}
              alt={title}
              className="mx-auto max-h-72 rounded-md object-contain"
            />
            <Button
              size="icon"
              variant="secondary"
              className="absolute right-1 top-1 h-7 w-7"
              onClick={onClear}
              aria-label="清除"
            >
              <X className="h-4 w-4" />
            </Button>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            className={cn(
              'flex h-48 w-full flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed',
              'border-muted-foreground/30 text-muted-foreground hover:border-primary hover:text-primary transition-colors'
            )}
          >
            <Upload className="h-7 w-7" />
            <span className="text-sm">点击上传 / 也可以直接拖进来</span>
          </button>
        )}
      </CardContent>
    </Card>
  );
}

export default function TryOnPage() {
  const { data: session } = useSession();
  const [personB64, setPersonB64] = useState<string | null>(null);
  const [personPreview, setPersonPreview] = useState<string | null>(null);
  const [garmentB64, setGarmentB64] = useState<string | null>(null);
  const [garmentPreview, setGarmentPreview] = useState<string | null>(null);
  const [prompt, setPrompt] = useState('');
  const [status, setStatus] = useState<TryOnStatus | null>(null);
  const [result, setResult] = useState<TryOnResult | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);

  const { data: wardrobe } = useItems({ is_archived: false }, 1, 60);

  useEffect(() => {
    applySessionToken(session);
    api.get<TryOnStatus>('/tryon/status').then(setStatus).catch(() => setStatus(null));
  }, [session]);

  const pickPerson = useCallback(async (f: File) => {
    const b64 = await fileToBase64(f);
    setPersonB64(b64);
    setPersonPreview(URL.createObjectURL(f));
    setError(null);
  }, []);

  const pickGarmentFile = useCallback(async (f: File) => {
    const b64 = await fileToBase64(f);
    setGarmentB64(b64);
    setGarmentPreview(URL.createObjectURL(f));
    setSelectedItemId(null);
    setError(null);
  }, []);

  const pickWardrobeItem = useCallback(async (item: Item) => {
    const url = item.image_url || item.medium_url || item.thumbnail_url;
    if (!url) return;
    setError(null);
    try {
      const b64 = await urlToBase64(url);
      setGarmentB64(b64);
      setGarmentPreview(url);
      setSelectedItemId(item.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : '读取衣橱图片失败');
    }
  }, []);

  const generate = useCallback(async () => {
    if (!personB64 || !garmentB64) {
      setError('请先上传本人照片和一件衣服');
      return;
    }
    applySessionToken(session);
    setIsGenerating(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.post<TryOnResult>('/tryon/generate', {
        person_image: personB64,
        garment_image: garmentB64,
        prompt: prompt.trim() || undefined,
      });
      setResult(res);
    } catch (e) {
      if (e instanceof ApiError) setError(e.message);
      else setError('生成失败，请检查中转站配置或稍后重试');
    } finally {
      setIsGenerating(false);
    }
  }, [personB64, garmentB64, prompt, session]);

  const download = useCallback(() => {
    if (!result) return;
    const a = document.createElement('a');
    a.href = `data:${result.mime_type};base64,${result.image_base64}`;
    a.download = `tryon-${Date.now()}.png`;
    a.click();
  }, [result]);

  const disabled = !status?.configured;

  return (
    <div className="container mx-auto max-w-6xl space-y-6 p-4 pb-24 lg:pb-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold">
            <Sparkles className="h-6 w-6 text-primary" /> 虚拟试穿
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            上传一张自己的正面照 + 一件衣服，AI 生成「你穿上它」的效果图。不限次数，照片只发给你配置的中转站。
          </p>
        </div>
        {status && (
          <Badge variant={status.configured ? 'default' : 'secondary'} className="gap-1">
            {status.configured ? <CheckCircle2 className="h-3 w-3" /> : <Settings2 className="h-3 w-3" />}
            {status.configured
              ? `${status.model} · ${status.mode}`
              : '未配置（需在 .env 设置 TRYON_*）'}
          </Badge>
        )}
      </div>

      {disabled && (
        <Alert>
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>
            虚拟试穿还没启用。在部署目录的 <code className="font-mono">.env</code> 里填好
            <code className="font-mono"> TRYON_ENABLED / TRYON_BASE_URL / TRYON_API_KEY / TRYON_MODEL / TRYON_MODE </code>
            并重启后端后即可使用。其余衣橱功能不受影响。
          </AlertDescription>
        </Alert>
      )}

      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="flex flex-col gap-4 md:flex-row">
        <ImageDrop
          title="① 本人照片"
          hint="正面、光线均匀、背景干净，头到大腿可见"
          icon={Camera}
          dataUrl={personPreview}
          onPick={pickPerson}
          onClear={() => { setPersonB64(null); setPersonPreview(null); }}
        />
        <ImageDrop
          title="② 衣服照片"
          hint="平铺/挂拍的单件衣服，图案完整；也可从下方衣橱直接选"
          icon={Shirt}
          dataUrl={garmentPreview}
          onPick={pickGarmentFile}
          onClear={() => { setGarmentB64(null); setGarmentPreview(null); setSelectedItemId(null); }}
        />
      </div>

      {wardrobe?.items && wardrobe.items.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">或从衣橱里挑一件</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex gap-2 overflow-x-auto pb-2">
              {wardrobe.items.map((item) => {
                const url = item.thumbnail_url || item.image_url;
                if (!url) return null;
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => pickWardrobeItem(item)}
                    className={cn(
                      'relative h-20 w-16 shrink-0 overflow-hidden rounded-md border-2',
                      selectedItemId === item.id ? 'border-primary' : 'border-transparent hover:border-muted-foreground/40'
                    )}
                    title={item.name || item.type}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={url} alt={item.name || item.type} className="h-full w-full object-cover" />
                  </button>
                );
              })}
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardContent className="space-y-3 pt-4">
          <div className="space-y-1">
            <Label htmlFor="tryon-prompt" className="text-xs text-muted-foreground">
              附加要求（可选）——留空则用默认指令
            </Label>
            <Textarea
              id="tryon-prompt"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="例如：保持平底鞋、把外套拉链拉上、背景换成街拍"
              rows={2}
            />
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={generate} disabled={isGenerating || disabled || !personB64 || !garmentB64}>
              {isGenerating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
              {isGenerating ? '生成中（约 10–60 秒）…' : '生成上身效果图'}
            </Button>
            {result && (
              <>
                <Button variant="outline" onClick={generate}>
                  <RefreshCw className="mr-2 h-4 w-4" /> 再来一张
                </Button>
                <Button variant="outline" onClick={download}>
                  <Download className="mr-2 h-4 w-4" /> 下载图片
                </Button>
              </>
            )}
          </div>
        </CardContent>
      </Card>

      {result && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <ImageIcon className="h-4 w-4" /> 生成结果
              <span className="text-xs font-normal text-muted-foreground">{result.model} · {result.mode}</span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={`data:${result.mime_type};base64,${result.image_base64}`}
              alt="试穿效果"
              className="mx-auto max-h-[560px] rounded-md object-contain"
            />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
