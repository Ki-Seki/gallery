# The Kiseki Gallery

一摞随机洗过的照片。拖走最上面那张看下一张，点开看大图。

https://ki-seki.github.io/gallery/

## 加照片

1. 在 GitHub 上进入 [`photos/`](photos) 文件夹 → **Add file → Upload files**，把照片拖进去，Commit。
2. 没了。Action 会自动：
   - 转正方向，长边缩到 2400px，重新压成渐进式 JPEG（GPS 等元数据会被去掉，只留拍摄时间和机型）
   - 生成 960px 的 WebP 缩略图到 `thumbs/`
   - 更新 `photos.json`，并把压缩结果提交回仓库
   - 部署到 GitHub Pages

支持 JPEG / PNG / HEIC / WebP / TIFF / AVIF。不小心传到仓库根目录也没关系，会被挪进 `photos/`。

**删照片**：直接删 `photos/` 里的文件，缩略图和清单会跟着清理。

> 网页上传单个文件上限 25 MB。更大的图（比如 40 MB 的修图导出）用 `git push`（上限 100 MB），或者先在本地跑一遍下面的脚本再提交，这样大原图也不会进 git 历史。

## 本地预览

```bash
pip install -r scripts/requirements.txt
python scripts/process.py
python -m http.server 4173
```

然后打开 http://localhost:4173 。

## 结构

```
index.html, assets/   页面（纯静态，无构建）
photos/               照片（上传到这里，会被原地压缩）
thumbs/               自动生成的缩略图
photos.json           自动生成的清单
scripts/process.py    压缩 + 缩略图 + 清单
.github/workflows/    压缩、提交、部署
```

压缩参数在 `scripts/process.py` 顶部（`MAX_EDGE`、`QUALITY`、`THUMB_EDGE`…）。
