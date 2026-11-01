param([Parameter(Mandatory=$true)][string]$ImagePath)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStreamWithContentType,Windows.Storage.Streams,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapTransform,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapBounds,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.PixelDataProvider,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
$awaitMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetGenericArguments().Count -eq 1 -and
    $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Await-WinRT($Operation, [Type]$ResultType) {
    $task = $awaitMethod.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $task.GetAwaiter().GetResult()
}
function Read-OcrBitmap($Decoder, $Transform, $Engine) {
    $image = Await-WinRT ($Decoder.GetSoftwareBitmapAsync(
        [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
        [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied,
        $Transform,
        [Windows.Graphics.Imaging.ExifOrientationMode]::IgnoreExifOrientation,
        [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage
    )) ([Windows.Graphics.Imaging.SoftwareBitmap])
    try {
        return Await-WinRT ($Engine.RecognizeAsync($image)) ([Windows.Media.Ocr.OcrResult])
    } finally { $image.Dispose() }
}
function Find-TextBounds($Decoder) {
    # Only retry empty OCR results. A short option surrounded by a full-width
    # white row can make Windows return no lines even when the glyphs are clear.
    Add-Type -TypeDefinition @'
public static class OcrTextBounds {
    public static int[] Find(byte[] pixels, int width, int height) {
        if (pixels.Length < 4 || pixels[0] < 235 || pixels[1] < 235 || pixels[2] < 235) return null;
        int left=width, top=height, right=-1, bottom=-1;
        for (int y=0; y<height; y++) for (int x=0; x<width; x++) {
            int p=(y*width+x)*4;
            if (pixels[p+3]<128 || (pixels[p]>210 && pixels[p+1]>210 && pixels[p+2]>210)) continue;
            if(x<left)left=x; if(x>right)right=x;
            if(y<top)top=y; if(y>bottom)bottom=y;
        }
        if(right<left) return null;
        left=System.Math.Max(0,left-16); top=System.Math.Max(0,top-16);
        right=System.Math.Min(width-1,right+16); bottom=System.Math.Min(height-1,bottom+16);
        if(right-left+1>width*0.9 && bottom-top+1>height*0.9) return null;
        return new int[]{left,top,right-left+1,bottom-top+1};
    }
}
'@
    $pixels = Await-WinRT ($Decoder.GetPixelDataAsync(
        [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
        [Windows.Graphics.Imaging.BitmapAlphaMode]::Straight,
        (New-Object Windows.Graphics.Imaging.BitmapTransform),
        [Windows.Graphics.Imaging.ExifOrientationMode]::IgnoreExifOrientation,
        [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage
    )) ([Windows.Graphics.Imaging.PixelDataProvider])
    return [OcrTextBounds]::Find($pixels.DetachPixelData(), $Decoder.PixelWidth, $Decoder.PixelHeight)
}
$stream = $null
try {
    $language = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | Where-Object { $_.LanguageTag -like 'zh-Hans*' } | Select-Object -First 1
    if (-not $language) { throw 'Chinese OCR language is not installed.' }
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
    if (-not $engine) { throw 'Chinese OCR engine is unavailable.' }
    $file = Await-WinRT ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
    $stream = Await-WinRT ($file.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
    $decoder = Await-WinRT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $transform = New-Object Windows.Graphics.Imaging.BitmapTransform
    $max = [double][Windows.Media.Ocr.OcrEngine]::MaxImageDimension
    $ratio = [Math]::Min(2.5, ($max - 1) / [Math]::Max($decoder.PixelWidth, $decoder.PixelHeight))
    $transform.ScaledWidth = [uint32][Math]::Max(1, [Math]::Round($decoder.PixelWidth * $ratio))
    $transform.ScaledHeight = [uint32][Math]::Max(1, [Math]::Round($decoder.PixelHeight * $ratio))
    $result = Read-OcrBitmap $decoder $transform $engine
    $recovered = $false
    if ([string]::IsNullOrWhiteSpace($result.Text)) {
        $rect = Find-TextBounds $decoder
        if ($rect) {
            foreach ($requestedRatio in @(1.5,1.0)) {
                $ratio = [Math]::Min($requestedRatio, ($max-1)/[Math]::Max($rect[2],$rect[3]))
                $transform = New-Object Windows.Graphics.Imaging.BitmapTransform
                $transform.ScaledWidth = [uint32][Math]::Max(1,[Math]::Round($decoder.PixelWidth*$ratio))
                $transform.ScaledHeight = [uint32][Math]::Max(1,[Math]::Round($decoder.PixelHeight*$ratio))
                # Bounds are in scaled coordinates (scale precedes crop).
                $bounds = New-Object Windows.Graphics.Imaging.BitmapBounds
                $bounds.X = [uint32][Math]::Floor($rect[0]*$ratio)
                $bounds.Y = [uint32][Math]::Floor($rect[1]*$ratio)
                $bounds.Width = [uint32][Math]::Min([Math]::Ceiling($rect[2]*$ratio),$transform.ScaledWidth-$bounds.X)
                $bounds.Height = [uint32][Math]::Min([Math]::Ceiling($rect[3]*$ratio),$transform.ScaledHeight-$bounds.Y)
                $transform.Bounds = $bounds
                $result = Read-OcrBitmap $decoder $transform $engine
                if (-not [string]::IsNullOrWhiteSpace($result.Text)) { $recovered=$true; break }
            }
        }
    }
    $words = @()
    foreach ($line in $result.Lines) {
        foreach ($word in $line.Words) {
            $words += @{
                text = $word.Text
                x = [double]$word.BoundingRect.X
                y = [double]$word.BoundingRect.Y
                w = [double]$word.BoundingRect.Width
                h = [double]$word.BoundingRect.Height
            }
        }
    }
    @{text=$result.Text; lines=@($result.Lines | ForEach-Object { $_.Text }); language=$language.LanguageTag;
      cropped_retry=$recovered; words=$words; scale=[double]$ratio;
      origin_width=[int]$decoder.PixelWidth; origin_height=[int]$decoder.PixelHeight} |
      ConvertTo-Json -Depth 5 -Compress
} finally {
    if ($stream) { $stream.Dispose() }
}
