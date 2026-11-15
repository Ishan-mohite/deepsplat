# Deploy the backend on Google Cloud

Cloud Run (API + processing) · Cloud Storage (photos, splats, VPS indexes) · Cloud SQL (Postgres) ·
Secret Manager (keys). Run from the repository root with the `gcloud` CLI logged in.

## 0. Variables

```bash
PROJECT=<your-project-id>
REGION=europe-west6                       # Zürich; any Cloud Run region works
BUCKET=$PROJECT-deepsplat
SA=deepsplat-backend@$PROJECT.iam.gserviceaccount.com
IMAGE=$REGION-docker.pkg.dev/$PROJECT/deepsplat/backend
DB_INSTANCE=deepsplat-db
DB_PASS=$(openssl rand -hex 16)
gcloud config set project $PROJECT
```

## 1. APIs, registry, bucket

```bash
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  sqladmin.googleapis.com storage.googleapis.com secretmanager.googleapis.com iamcredentials.googleapis.com

gcloud artifacts repositories create deepsplat --repository-format=docker --location=$REGION

gcloud storage buckets create gs://$BUCKET --location=$REGION --uniform-bucket-level-access
gcloud storage buckets update gs://$BUCKET --cors-file=backend/deploy/cors.json
```

The bucket stays private: the app uploads and the viewer downloads through signed URLs.
CORS lets browsers use those URLs (the viewer loads the splat directly from the bucket).

## 2. Service account

```bash
gcloud iam service-accounts create deepsplat-backend
gcloud storage buckets add-iam-policy-binding gs://$BUCKET --member=serviceAccount:$SA --role=roles/storage.objectAdmin
gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$SA --role=roles/cloudsql.client
gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$SA --role=roles/secretmanager.secretAccessor
# signing URLs without a key file: the service account signs as itself
gcloud iam service-accounts add-iam-policy-binding $SA --member=serviceAccount:$SA \
  --role=roles/iam.serviceAccountTokenCreator
```

## 3. Database

```bash
gcloud sql instances create $DB_INSTANCE --database-version=POSTGRES_16 --edition=ENTERPRISE \
  --tier=db-f1-micro --region=$REGION
gcloud sql databases create deepsplat --instance=$DB_INSTANCE
gcloud sql users create deepsplat --instance=$DB_INSTANCE --password=$DB_PASS
```

Tables are created by the backend on startup.

## 4. Secrets

```bash
printf '%s' "postgresql+psycopg://deepsplat:$DB_PASS@/deepsplat?host=/cloudsql/$PROJECT:$REGION:$DB_INSTANCE" \
  | gcloud secrets create database-url --data-file=-
printf '%s' "<roboflow api key>" | gcloud secrets create roboflow-api-key --data-file=-
openssl rand -hex 24 | tr -d '\n' | gcloud secrets create api-token --data-file=-   # token for the app
```

## 5. Build and deploy

```bash
gcloud builds submit --config backend/deploy/cloudbuild.yaml --substitutions _IMAGE=$IMAGE .

gcloud run deploy deepsplat-backend --image $IMAGE --region $REGION --service-account $SA \
  --cpu 2 --memory 4Gi --timeout 900 \
  --no-cpu-throttling --min-instances 1 --max-instances 3 \
  --add-cloudsql-instances $PROJECT:$REGION:$DB_INSTANCE \
  --set-env-vars STORAGE=gcs,GCS_BUCKET=$BUCKET,VPS=sift,DETECTOR=roboflow_sam3,DATA_DIR=/tmp/deepsplat \
  --set-secrets DATABASE_URL=database-url:latest,ROBOFLOW_API_KEY=roboflow-api-key:latest,API_TOKEN=api-token:latest \
  --allow-unauthenticated

URL=$(gcloud run services describe deepsplat-backend --region $REGION --format 'value(status.url)')
gcloud run services update deepsplat-backend --region $REGION --update-env-vars PUBLIC_BASE_URL=$URL
curl $URL/health
```

Why these flags:
* `--no-cpu-throttling --min-instances 1`: uploads from the app are processed after the
  `202` response (background task). With CPU throttling, Cloud Run would freeze that work.
* `--cpu 2 --memory 4Gi`: SAM 3 runs at Roboflow; the instance does VPS (SIFT, ~1 s per frame),
  lifting and PDFs. Enough for room-sized maps.
* `--allow-unauthenticated`: the API checks the bearer token itself (`API_TOKEN`); the viewer's
  read endpoints are open.
* `/tmp/deepsplat`: local cache only. Everything durable is in the bucket and the database
  (VPS indexes are pushed to `gs://$BUCKET/vps/` and pulled by new instances).

## 6. Load a map (large splat)

Cloud Run accepts at most 32 MB per request, so the splat goes straight to the bucket:

```bash
TOKEN=$(gcloud secrets versions access latest --secret=api-token)
H="Authorization: Bearer $TOKEN"
curl -s -X POST $URL/maps/upload-urls -H "$H" -H 'Content-Type: application/json' \
  -d '{"map_id": "unit-3-04"}' > urls.json
curl -X PUT -H 'Content-Type: application/octet-stream' --upload-file point_cloud.ply "$(jq -r .splat.upload_url urls.json)"
curl -X PUT -H 'Content-Type: application/zip' --upload-file views.zip "$(jq -r .views.upload_url urls.json)"
curl -X POST $URL/maps/register -H "$H" -H 'Content-Type: application/json' -d '{
  "map_id": "unit-3-04", "name": "Unit 3.04",
  "metadata": {"property": "Property A", "floor_label": "3rd floor", "up_axis": "z", "north": [0, 1]}}'
curl $URL/maps/unit-3-04        # wait for status and vps_status "ready"
```

## 7. iOS app settings

Backend URL = `$URL`, token = the `api-token` secret, map ID = the `map_id` above.

## Later

* GPU / hloc VPS: build with `--substitutions _IMAGE=$IMAGE,_WITH_VPS=1`, deploy with `VPS=hloc`
  and a GPU (`--gpu 1 --gpu-type nvidia-l4`, in a region that offers Cloud Run GPUs).
* Replace the background task with Cloud Tasks if captures arrive faster than one instance handles.
