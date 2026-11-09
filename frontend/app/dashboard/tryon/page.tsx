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
  Plus,
  Ruler,
  ShieldCheck,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Switch } from '@/components/ui/switch';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { api, ApiError, getAccessToken } from '@/lib/api';
import { applySessionToken } from '@/lib/hooks/use-session-token';
import { useItems } from '@/lib/hooks/use-items';
import type { Item } from '@/lib/types';
import { cn } from '@/lib/utils';

const MAX_PHOTOS = 6;

interface TryOnStatus {
  enabled: boolean;
  configured: boolean;
  model: string;
  mode: string;
  base_url_host: string | null;
  max_person_photos: number;
}

interface TryOnResult {
  image_base64: string;
  mime_type: string;
  model: string;
  mode: string;
  person_photo_count: number;
}

interface PersonPhoto {
  id: string;
  b64: string;
  preview: string;
}

interface BodyProfile {
  height_cm?: number;
  weight_kg?: number;
  bust_cm?: number;
  waist_cm?: number;
  hip_cm?: number;
  build?: string;
  notes?: string;
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

function GarmentDrop({
  dataUrl,
  onPick,
  onClear,
}: {
  dataUrl: string | null;
  onPick: (f: File) => void;
  onClear: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <Card className="flex-1 min-w-[240px]">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <Shirt className="h-4 w-4" /> 衣服照片
        </CardTitle>
        <CardDescription className="text-xs">平铺/挂拍的单件衣服，图案完整；也可从下方衣橱直接选</CardDescription>
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
            <img src={dataUrl} alt="衣服" className="mx-auto max-h-72 rounded-md object-contain" />
            <Button size="icon" variant="secondary" className="absolute right-1 top-1 h-7 w-7" onClick={onClear} aria-label="清除">
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
            <span className="text-sm">点击上传 / 拖入图片</span>
          </button>
        )}
      </CardContent>
    </Card>
  );
}

export default function TryOnPage() {
  const { data: session } = useSession();
  const [photos, setPhotos] = useState<PersonPhoto[]>([]);
  const [garmentB64, setGarmentB64] = useState<string | null>(null);
  const [garmentPreview, setGarmentPreview] = useState<string | null>(null);
  const [prompt, setPrompt] = useState('');
  const [status, setStatus] = useState<TryOnStatus | null>(null);
  const [result, setResult] = useState<TryOnResult | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [preserveFace, setPreserveFace] = useState(true);
  const [showBody, setShowBody] = useState(false);
  const [body, setBody] = useState<BodyProfile>({});
  const personInputRef = useRef<HTMLInputElement>(null);
  const photosRef = useRef<PersonPhoto[]>([]);
  photosRef.current = photos;

  const { data: wardrobe } = useItems({ is_archived: false }, 1, 60);

  useEffect(() => {
    applySessionToken(session);
    api.get<TryOnStatus>('/tryon/status').then(setStatus).catch(() => setStatus(null));
  }, [session]);

  const addPersonPhotos = useCallback(async (files: FileList | File[]) => {
    const arr = Array.from(files);
    const slots = MAX_PHOTOS - photosRef.current.length;
    const picked = arr.slice(0, Math.max(0, slots));
    const decoded = await Promise.all(
      picked.map(async (f) => ({
        id: `${Date.now()}-${Math.random()}`,
        b64: await fileToBase64(f),
        preview: URL.createObjectURL(f),
      }))
    );
    setPhotos((cur) => [...cur, ...decoded].slice(0, MAX_PHOTOS));
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
    if (!photos.length || !garmentB64) {
      setError('请至少上传一张本人照片和一件衣服');
      return;
    }
    applySessionToken(session);
    setIsGenerating(true);
    setError(null);
    setResult(null);
    try {
      const cleanBody: BodyProfile = {};
      (['height_cm', 'weight_kg', 'bust_cm', 'waist_cm', 'hip_cm'] as const).forEach((k) => {
        const v = body[k];
        if (v && Number(v) > 0) cleanBody[k] = Number(v);
      });
      if (body.build?.trim()) cleanBody.build = body.build.trim();
      if (body.notes?.trim()) cleanBody.notes = body.notes.trim();

      const res = await api.post<TryOnResult>('/tryon/generate', {
        person_images: photos.map((p) => p.b64),
        garment_image: garmentB64,
        prompt: prompt.trim() || undefined,
        body: Object.keys(cleanBody).length ? cleanBody : undefined,
        preserve_face: preserveFace,
      });
      setResult(res);
    } catch (e) {
      if (e instanceof ApiError) setError(e.message);
      else setError('生成失败，请检查中转站配置或稍后重试');
    } finally {
      setIsGenerating(false);
    }
  }, [photos, garmentB64, prompt, body, preserveFace, session]);

  const download = useCallback(() => {
    if (!result) return;
    const a = document.createElement('a');
    a.href = `data:${result.mime_type};base64,${result.image_base64}`;
    a.download = `tryon-${Date.now()}.png`;
    a.click();
  }, [result]);

  const disabled = !status?.configured;
  const maxPhotos = status?.max_person_photos ?? MAX_PHOTOS;

  return (
    <div className="container mx-auto max-w-6xl space-y-6 p-4 pb-24 lg:pb-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold">
            <Sparkles className="h-6 w-6 text-primary" /> 虚拟试穿
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            上传 1–{maxPhotos} 张自己的照片（正面、侧面等多角度更准）+ 一件衣服，AI 生成「你穿上它」的效果图。照片只发给你配置的中转站。
          </p>
        </div>
        {status && (
          <Badge variant={status.configured ? 'default' : 'secondary'} className="gap-1">
            {status.configured ? <CheckCircle2 className="h-3 w-3" /> : <Settings2 className="h-3 w-3" />}
            {status.configured ? `${status.model} · ${status.mode}` : '未配置（需在 .env 设置 TRYON_*）'}
          </Badge>
        )}
      </div>

      {disabled && (
        <Alert>
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>
            虚拟试穿还没启用。在部署目录的 <code className="font-mono">.env</code> 里填好
            <code className="font-mono"> TRYON_* </code>并重启后端后即可使用。其余衣橱功能不受影响。
          </AlertDescription>
        </Alert>
      )}

      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* ① 多张本人照片 */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Camera className="h-4 w-4" /> ① 本人照片
            <span className="ml-auto text-xs font-normal text-muted-foreground">
              {photos.length}/{maxPhotos} 张 · 至少 1 张
            </span>
          </CardTitle>
          <CardDescription className="text-xs">
            建议：正面 + 侧面 + 半身，光线均匀、背景干净、脸清晰。多角度能明显提升人脸与身材的一致性。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <input
            ref={personInputRef}
            type="file"
            accept="image/*"
            multiple
            className="hidden"
            onChange={(e) => {
              if (e.target.files) addPersonPhotos(e.target.files);
              e.target.value = '';
            }}
          />
          <div className="flex flex-wrap gap-3">
            {photos.map((p, idx) => (
              <div key={p.id} className="relative h-40 w-28 overflow-hidden rounded-md border">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={p.preview} alt={`本人照片 ${idx + 1}`} className="h-full w-full object-cover" />
                <span className="absolute bottom-0 left-0 rounded-tr-md bg-black/60 px-1.5 text-[10px] text-white">
                  {idx === 0 ? '主图' : `#${idx + 1}`}
                </span>
                <Button
                  size="icon"
                  variant="secondary"
                  className="absolute right-0.5 top-0.5 h-6 w-6"
                  onClick={() => setPhotos((cur) => cur.filter((x) => x.id !== p.id))}
                  aria-label="移除"
                >
                  <X className="h-3 w-3" />
                </Button>
              </div>
            ))}
            {photos.length < maxPhotos && (
              <button
                type="button"
                onClick={() => personInputRef.current?.click()}
                className={cn(
                  'flex h-40 w-28 flex-col items-center justify-center gap-1 rounded-md border-2 border-dashed',
                  'border-muted-foreground/30 text-muted-foreground hover:border-primary hover:text-primary transition-colors'
                )}
              >
                <Plus className="h-6 w-6" />
                <span className="text-xs">添加照片</span>
              </button>
            )}
          </div>
        </CardContent>
      </Card>

      {/* ② 衣服 */}
      <div className="flex flex-col gap-4 md:flex-row">
        <GarmentDrop
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

      {/* ③ 身材信息（可选）*/}
      <Collapsible open={showBody} onOpenChange={setShowBody}>
        <Card>
          <CollapsibleTrigger asChild>
            <CardHeader className="cursor-pointer pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <Ruler className="h-4 w-4" /> 身材信息（可选，帮助还原体型与合身度）
                <span className="ml-auto text-xs font-normal text-muted-foreground">{showBody ? '收起' : '展开'}</span>
              </CardTitle>
            </CardHeader>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <CardContent className="space-y-3 pt-0">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
                {([
                  ['height_cm', '身高 cm'],
                  ['weight_kg', '体重 kg'],
                  ['bust_cm', '胸围 cm'],
                  ['waist_cm', '腰围 cm'],
                  ['hip_cm', '臀围 cm'],
                ] as const).map(([key, label]) => (
                  <div key={key} className="space-y-1">
                    <Label htmlFor={`body-${key}`} className="text-xs text-muted-foreground">{label}</Label>
                    <Input
                      id={`body-${key}`}
                      type="number"
                      inputMode="decimal"
                      value={body[key] ?? ''}
                      onChange={(e) => setBody((b) => ({ ...b, [key]: e.target.value === '' ? undefined : Number(e.target.value) }))}
                      placeholder="—"
                    />
                  </div>
                ))}
              </div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="space-y-1">
                  <Label htmlFor="body-build" className="text-xs text-muted-foreground">体型（偏瘦 / 匀称 / 健壮…）</Label>
                  <Input id="body-build" value={body.build ?? ''} onChange={(e) => setBody((b) => ({ ...b, build: e.target.value }))} placeholder="选填" />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="body-notes" className="text-xs text-muted-foreground">其他特征（肩宽、发型、肤色…）</Label>
                  <Input id="body-notes" value={body.notes ?? ''} onChange={(e) => setBody((b) => ({ ...b, notes: e.target.value }))} placeholder="选填" />
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                这些数值会作为文字提示帮助模型把握体型与衣服松紧，<strong>以照片为准</strong>，不会真的按厘米建模。
              </p>
            </CardContent>
          </CollapsibleContent>
        </Card>
      </Collapsible>

      {/* ④ 生成选项 */}
      <Card>
        <CardContent className="space-y-3 pt-4">
          <div className="flex items-center justify-between rounded-md border px-3 py-2">
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-primary" />
              <div>
                <div className="text-sm font-medium">锁定人脸（input_fidelity=high）</div>
                <div className="text-xs text-muted-foreground">尽量保持五官与参考照一致；关掉会更快但更容易「换脸」</div>
              </div>
            </div>
            <Switch checked={preserveFace} onCheckedChange={setPreserveFace} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="tryon-prompt" className="text-xs text-muted-foreground">附加要求（可选）</Label>
            <Textarea
              id="tryon-prompt"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="例如：外套拉链拉上、换成街拍背景、只要正面"
              rows={2}
            />
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={generate} disabled={isGenerating || disabled || !photos.length || !garmentB64}>
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
              <span className="text-xs font-normal text-muted-foreground">
                {result.model} · {result.mode} · 用了 {result.person_photo_count} 张人像
              </span>
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
